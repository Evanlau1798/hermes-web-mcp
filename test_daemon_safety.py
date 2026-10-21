import json
import queue
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import runtime
from websockets.client import ClientProtocol
from websockets.server import ServerProtocol
from websockets.uri import parse_uri
from daemon_rpc import DaemonRPC, DaemonError
from daemon_adapter import DaemonAdapter
from test_daemon_route import ID
import test_daemon_route as fixtures


def wire():
    client = ClientProtocol(parse_uri('ws://localhost/'))
    server = ServerProtocol()
    client.send_request(client.connect())
    server.receive_data(b''.join(client.data_to_send()))
    request = server.events_received()[0]
    server.send_response(server.accept(request))
    client.receive_data(b''.join(server.data_to_send()))
    client.events_received()
    rpc = DaemonRPC()
    rpc.protocol = client
    rpc.chunks = queue.Queue()
    rpc.proc = Mock()
    return rpc,server


class WireTests(unittest.TestCase):
    def test_fragmented_chinese_notification_and_native_approval_is_blocked(self):
        rpc,server = wire()
        data = json.dumps({'method':'test','params':{'text':'繁體中文'}},ensure_ascii=False).encode()
        server.send_text(data[:9],fin=False)
        server.send_continuation(data[9:],fin=True)
        server.send_text(json.dumps({'id':500,'method':'item/commandExecution/requestApproval'}).encode())
        chunks = b''.join(server.data_to_send())
        rpc.chunks.put(chunks[:5]); rpc.chunks.put(chunks[5:])
        rpc._receive(1); rpc._receive(1)
        self.assertEqual(rpc.pending_messages[0]['params']['text'],'繁體中文')
        self.assertEqual(rpc.blocked_requests,1)
        writes = b''.join(c.args[0] for c in rpc.proc.stdin.write.call_args_list)
        server.receive_data(writes)
        response = json.loads(server.events_received()[0].data)
        self.assertIn('error',response)
        self.assertNotIn('result',response)

    def test_completion_arriving_before_rpc_ack_is_retained(self):
        rpc,server = wire()
        server.send_text(json.dumps({'method':'turn/completed','params':{
            'threadId':ID,'turn':{'id':'own-turn','status':'completed'}}}).encode())
        server.send_text(b'{"id":1,"result":{"turn":{"id":"own-turn"}}}')
        rpc.chunks.put(b''.join(server.data_to_send()))
        self.assertEqual(rpc.request('turn/start',{})['turn']['id'],'own-turn')
        self.assertTrue(any(e.get('method')=='turn/completed' for e in rpc.completed_events))

    def test_timeout_after_write_is_unknown_but_native_rejection_is_not_sent(self):
        rpc,server = wire()
        with self.assertRaises(DaemonError) as caught:
            rpc.request('turn/start',{},timeout=.01)
        self.assertTrue(caught.exception.sent)
        rpc,server = wire()
        server.send_text(b'{"id":1,"error":{"code":-32600,"message":"native refused"}}')
        rpc.chunks.put(b''.join(server.data_to_send()))
        with self.assertRaises(DaemonError) as caught:
            rpc.request('turn/start',{})
        self.assertEqual(caught.exception.code,'DAEMON_REJECTED')
        self.assertFalse(caught.exception.sent)


class SettingsTests(unittest.TestCase):
    @unittest.skipUnless(__import__('os').name=='nt','Windows extended path')
    def test_extended_windows_rollout_path_stays_inside_allowed_root(self):
        import sqlite3
        from context_usage import read_context_usage
        from resume_settings import read_resume_settings
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'sessions').mkdir()
            log=root/'sessions/test.jsonl'
            log.write_text('\n'.join(json.dumps(e) for e in [
                {'type':'turn_context','payload':{'approval_policy':'never','sandbox_policy':{'type':'danger-full-access'}}},
                {'type':'event_msg','payload':{'type':'task_started','turn_id':'baseline'}},
                {'type':'event_msg','payload':{'type':'token_count','info':{'last_token_usage':{'total_tokens':90001}}}}
            ]),encoding='utf-8')
            with __import__('sqlite3').connect(root/'state_5.sqlite') as db:
                db.execute('CREATE TABLE threads (id TEXT,rollout_path TEXT)')
                db.execute('INSERT INTO threads VALUES (?,?)',(ID,'\\\\?\\'+str(log)))
            db.close()
            self.assertEqual(read_context_usage(ID,'baseline',root)['contextTokens'],90001)
            with patch('resume_settings.home',return_value=root):
                self.assertEqual(read_resume_settings(ID)['approvalPolicy'],'never')

    def test_known_cold_resume_checks_persisted_permissions_then_only_loads_same_id(self):
        fixture = fixtures.AdapterTests()
        replies = fixture.replies()
        replies['thread/loaded/list']['data'] = []
        replies['thread/resume'].update(approvalPolicy='never',sandbox={'type':'dangerFullAccess'})
        adapter,client = fixture.adapter(replies)
        adapter.resume_settings = lambda _: {'approvalPolicy':'never','sandbox':{'type':'danger-full-access'}}
        result = adapter('load',{'thread_id':ID,'expected_title':'Exact title'})
        self.assertIsNone(result.get('errorCode'))
        params = next(c.args[1] for c in client.request.call_args_list if c.args[0]=='thread/resume')
        self.assertEqual(params,{'threadId':ID,'excludeTurns':True})

    def test_preflight_timeout_does_not_claim_message_may_have_been_sent(self):
        client = Mock()
        client.__enter__=Mock(return_value=client)
        client.__exit__=Mock(return_value=False)
        client.request.side_effect=DaemonError('DAEMON_TIMEOUT',True)
        adapter=DaemonAdapter(client_factory=lambda:client,identity=lambda:'instance')
        result=adapter('send',{'thread_id':ID,'expected_title':'Exact title','message':'private'})
        self.assertEqual(result['deliveryState'],'not-sent')

    def test_daemon_read_redacts_credentials_before_truncation(self):
        fixture=fixtures.AdapterTests(); replies=fixture.replies()
        replies['thread/turns/list']['data'][0]['items'][0]['text']='sk-'+'a'*40
        adapter,client=fixture.adapter(replies)
        result=adapter('read',{'thread_id':ID,'max_chars':12})
        self.assertNotIn('sk-',result['turns'][0]['messages'][0]['text'])

    def test_stop_reports_partial_when_goal_pause_succeeds_but_cleanup_fails(self):
        fixture=fixtures.AdapterTests(); replies=fixture.replies()
        replies['thread/read']['thread']['status']['type']='active'
        replies['thread/goal/get']={'goal':{'status':'active'}}
        replies['thread/goal/set']={}; replies['turn/interrupt']={}
        adapter,client=fixture.adapter(replies)
        original=client.request.side_effect
        def call(method,args):
            if method=='thread/backgroundTerminals/clean':
                raise DaemonError('DAEMON_CAPABILITY_UNAVAILABLE')
            return original(method,args)
        client.request.side_effect=call
        result=adapter('stop',{'thread_id':ID,'expected_title':'Exact title'})
        self.assertEqual(result['deliveryState'],'partial')
        self.assertFalse(result['stopConfirmed'])
        self.assertTrue(result['goalPauseConfirmed'])


if __name__=='__main__':
    unittest.main()
