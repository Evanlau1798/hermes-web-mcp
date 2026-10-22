import json
import tempfile
import unittest

import backend
from compact_jobs import CompactJobs
from operation_summary import observe_turn
from test_compact_jobs import FakeAPI, snapshot, wait_for, THREAD_ID


AUTH='Error running remote compact task: Your access token could not be refreshed because you have since logged out or signed in to another account. Please sign in again.'


class CompactDiagnosticsTests(unittest.TestCase):
    def test_absent_native_error_does_not_invent_one(self):
        from compact_snapshot import read_snapshot
        data=snapshot(turn_state='failed')
        current,error=read_snapshot(lambda *a:data,THREAD_ID,'Exact title',{})
        self.assertIsNone(error)
        self.assertIsNone(current.get('diagnostic'))
        row={'thread_id':THREAD_ID,'baseline_turn_id':'old','compact_turn_id':None,'compact_complete':False}
        observed=observe_turn(lambda *a:data,row)
        self.assertEqual(observed['state'],'failed')
        self.assertNotIn('error_details',observed)

    def test_daemon_snapshot_and_rpc_retain_native_failure_without_private_text(self):
        import test_daemon_route
        from daemon_rpc import DaemonRPC,DaemonError
        from unittest.mock import Mock
        helper=test_daemon_route.AdapterTests()
        replies=helper.replies()
        replies['thread/turns/list']['data'][0].update(status='failed',error={'message':AUTH+' PRIVATE'})
        adapter,_=helper.adapter(replies)
        result=adapter('status',{'thread_id':test_daemon_route.ID})
        self.assertEqual(result['latestTurn'].get('nativeError',{}).get('errorCode'),'AUTHENTICATION_REQUIRED')
        rpc=DaemonRPC()
        rpc._send=Mock()
        rpc.pending_messages=[{'id':1,'error':{'code':-32001,'message':AUTH+' PRIVATE'}}]
        with self.assertRaises(DaemonError) as caught:
            rpc.request('thread/compact/start',{'threadId':THREAD_ID})
        self.assertEqual(caught.exception.code,'AUTHENTICATION_REQUIRED')
        self.assertEqual(caught.exception.details['nativeCode'],-32001)
        self.assertEqual(caught.exception.details['nativeMethod'],'thread/compact/start')
        self.assertFalse(caught.exception.sent)
        self.assertNotIn('PRIVATE',json.dumps(caught.exception.details))

    def test_failed_native_compact_preserves_safe_cause_across_restart_without_send(self):
        failed=snapshot(turn_id='failed-compact',turn_state='failed',completed_at=124)
        failed['latestTurn']['nativeError']={'message':AUTH+' Bearer PRIVATE-TOKEN','requestId':'native-request',
                                            'source':'codex-daemon-live'}
        api=FakeAPI([snapshot(),snapshot(),snapshot(),failed])
        with tempfile.TemporaryDirectory() as folder:
            manager=CompactJobs(api,root=folder,sleep=lambda _:None,recover=False)
            manager.prepare(THREAD_ID,'Exact title','PRIVATE MESSAGE','diag-compact-0001')
            manager.confirm('diag-compact-0001')
            self.assertTrue(wait_for(lambda:manager.status('diag-compact-0001')['state']=='compaction_failed'))
            receipt=manager.status('diag-compact-0001')
            self.assertEqual(receipt['delivery_state'],'not-sent')
            details=receipt.get('error_details') or {}
            self.assertEqual(details.get('errorCode'),'AUTHENTICATION_REQUIRED')
            self.assertEqual(details.get('requestId'),'native-request')
            self.assertEqual(details.get('relatedTurnId'),'failed-compact')
            self.assertNotIn('PRIVATE',json.dumps(receipt))
            self.assertFalse(any(c[0]=='send' for c in api.calls))
            manager.close()
            restarted=CompactJobs(lambda *a:self.fail('terminal lookup must not dispatch'),root=folder,recover=False)
            self.assertEqual(restarted.status('diag-compact-0001')['error_details'],details)
            restarted.close()

    def test_rejected_start_keeps_original_error_and_unknown_start_is_never_replayed(self):
        for delivery in ('rejected','unknown'):
            api=FakeAPI([snapshot(),snapshot(),snapshot()],start={
                'deliveryState':delivery,'errorCode':'BACKEND_WRITER_CONFLICT','stage':'request','requestId':'compact-request'})
            with tempfile.TemporaryDirectory() as folder:
                manager=CompactJobs(api,root=folder,sleep=lambda _:None,recover=False)
                manager.prepare(THREAD_ID,'Exact title','private','diag-start-0001')
                manager.confirm('diag-start-0001')
                self.assertTrue(wait_for(lambda:not manager.status('diag-start-0001')['worker_alive']))
                receipt=manager.status('diag-start-0001')
                self.assertEqual(receipt.get('error_details',{}).get('requestId'),'compact-request')
                self.assertEqual(receipt['state'],'compaction_failed' if delivery=='rejected' else 'compaction_unknown')
                manager.confirm('diag-start-0001')
                self.assertEqual(sum(c[0]=='compact_start' for c in api.calls),1)
                self.assertFalse(any(c[0]=='send' for c in api.calls))
                manager.close()

    def test_observation_preserves_pinned_failure_details_without_retry(self):
        calls=[]
        def api(action,args):
            calls.append((action,args))
            return {'ok':False,'errorCode':'BACKEND_INSTANCE_CHANGED','stage':'owner','requestId':'status-request'}
        row={'thread_id':THREAD_ID,'execution_backend':'daemon','backend_instance_id':'original-instance'}
        result=observe_turn(api,row)
        self.assertEqual(result['state'],'unknown')
        self.assertEqual(result.get('error_details',{}).get('requestId'),'status-request')
        self.assertEqual(calls,[('status',{'thread_id':THREAD_ID,'execution_backend':'daemon','backend_instance_id':'original-instance'})])

    def test_poll_failure_preserves_nested_adapter_diagnostics_without_replaying(self):
        api=FakeAPI([snapshot(),snapshot(),snapshot(),{'ok':False,'errorCode':'DAEMON_REJECTED',
            'error_details':{'errorCode':'AUTHENTICATION_REQUIRED','requestId':'nested-request',
                             'nativeMethod':'thread/read','nativeCode':-32001}}])
        with tempfile.TemporaryDirectory() as folder:
            manager=CompactJobs(api,root=folder,sleep=lambda _:None,recover=False)
            manager.prepare(THREAD_ID,'Exact title','private','diag-poll-0001')
            manager.confirm('diag-poll-0001')
            self.assertTrue(wait_for(lambda:not manager.status('diag-poll-0001')['worker_alive']))
            receipt=manager.status('diag-poll-0001')
            self.assertEqual(receipt['state'],'compaction_unknown')
            self.assertEqual(receipt['error_details']['requestId'],'nested-request')
            self.assertEqual(receipt['error_details']['nativeCode'],-32001)
            self.assertEqual(receipt['delivery_state'],'not-sent')
            self.assertEqual(sum(c[0]=='compact_start' for c in api.calls),1)
            self.assertFalse(any(c[0]=='send' for c in api.calls))
            manager.close()


if __name__=='__main__':
    unittest.main()
