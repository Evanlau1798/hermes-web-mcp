import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import backend
from codex_router import CodexRouter
from test_daemon_route import ID, snapshot


class ReadRecoveryTests(unittest.TestCase):
    def test_valid_desktop_read_survives_stale_daemon_but_writes_fail_closed(self):
        desktop = Mock(return_value=snapshot('active', 'desktop'))
        daemon = Mock()
        daemon.probe.return_value = {'available':False,'errorCode':'DAEMON_IDENTITY_UNCONFIRMED'}
        router = CodexRouter(desktop,daemon)
        for action in ('status','read'):
            result = router(action,{'thread_id':ID})
            self.assertEqual(result.get('thread',{}).get('id'),ID)
            self.assertTrue(result.get('liveStatusConfirmed'))
            self.assertEqual(result['ownerVerification']['state'],'unknown')
        self.assertEqual(router('send',{'thread_id':ID})['errorCode'],'DAEMON_IDENTITY_UNCONFIRMED')
        daemon.assert_not_called()

    def test_read_rejection_does_not_switch_to_daemon_or_load_target(self):
        rejected={'ok':False,'errorCode':'DESKTOP_REJECTED','stage':'request','requestId':'native-request'}
        desktop=Mock(return_value=rejected)
        daemon=Mock()
        daemon.probe.return_value={'available':True,'loaded':True,'instance_id':'other'}
        router=CodexRouter(desktop,daemon)
        result=router('read',{'thread_id':ID})
        self.assertEqual(result.get('errorCode'),'DESKTOP_REJECTED')
        self.assertEqual(result.get('requestId'),'native-request')
        self.assertFalse(result.get('liveStatusConfirmed',True))
        daemon.assert_not_called()
        self.assertEqual(desktop.call_count,1)

    def test_status_succeeds_then_read_rejection_keeps_original_request(self):
        desktop=Mock(side_effect=[snapshot('active','desktop'),
            {'ok':False,'errorCode':'DESKTOP_REJECTED','stage':'request','requestId':'read-only-failure'}])
        daemon=Mock()
        daemon.probe.return_value={'available':False,'errorCode':'DAEMON_UNAVAILABLE'}
        with patch('codex_router.history_snapshot',return_value=None):
            result=CodexRouter(desktop,daemon)('read',{'thread_id':ID})
        self.assertEqual(result['requestId'],'read-only-failure')
        self.assertFalse(result['liveStatusConfirmed'])
        self.assertEqual([c.args[0] for c in desktop.call_args_list],['status','read'])
        daemon.assert_not_called()

    def fixture(self,folder,record_id=ID):
        home=Path(folder)
        path=home/'sessions/fixture.jsonl'
        path.parent.mkdir()
        events=[{'type':'session_meta','payload':{'id':record_id}},
                {'type':'event_msg','timestamp':'2026-01-01T01:02:03Z',
                 'payload':{'type':'agent_message','message':'Public message Bearer fixture-secret'}},
                {'type':'event_msg','timestamp':'2026-01-01T01:02:04Z',
                 'payload':{'type':'token_count','info':{'total_tokens':999}}},
                {'type':'event_msg','timestamp':'2026-01-01T01:02:05Z',
                 'payload':{'type':'agent_message','phase':'analysis','message':'PRIVATE reasoning'}}]
        path.write_text('\n'.join(json.dumps(e) for e in events)+'\n',encoding='utf-8')
        db=sqlite3.connect(home/'state_5.sqlite')
        db.execute('CREATE TABLE threads (id TEXT,rollout_path TEXT)')
        db.execute('INSERT INTO threads VALUES (?,?)',(ID,str(path)))
        db.commit();db.close()
        return home

    def test_failed_live_read_returns_labeled_public_history_without_live_status(self):
        from rollout_history import history_snapshot
        with tempfile.TemporaryDirectory() as folder:
            home=self.fixture(folder)
            result=history_snapshot(ID,home=home)
            self.assertEqual(result['source'],'codex-rollout-history')
            self.assertFalse(result['liveStatusConfirmed'])
            self.assertEqual(result['snapshotAsOf'],'2026-01-01T01:02:03Z')
            self.assertIn('歷史快照',result['notice'])
            self.assertNotIn('status',result)
            self.assertNotIn('fixture-secret',json.dumps(result))
            desktop=Mock(return_value={'ok':False,'errorCode':'DESKTOP_REJECTED','requestId':'failed-read'})
            with patch('codex_router.history_snapshot',return_value=result):
                routed=CodexRouter(desktop,Mock())('read',{'thread_id':ID})
            self.assertEqual(routed['historicalSnapshot'],result)
            self.assertEqual(routed['errorCode'],'DESKTOP_REJECTED')

    def test_history_rejects_mismatched_metadata_and_external_path(self):
        from rollout_history import history_snapshot
        with tempfile.TemporaryDirectory() as folder:
            home=self.fixture(folder,'22222222-2222-4222-8222-222222222222')
            self.assertIsNone(history_snapshot(ID,home=home))
            db=sqlite3.connect(home/'state_5.sqlite')
            db.execute('UPDATE threads SET rollout_path=?',(str(home/'outside.jsonl'),))
            db.commit();db.close()
            self.assertIsNone(history_snapshot(ID,home=home))

    def test_response_items_are_supported_and_total_json_is_bounded(self):
        from rollout_history import history_snapshot
        with tempfile.TemporaryDirectory() as folder:
            home=self.fixture(folder)
            path=home/'sessions/fixture.jsonl'
            with path.open('a',encoding='utf-8') as stream:
                for _ in range(20):
                    stream.write(json.dumps({'type':'response_item','timestamp':'2026-01-02T00:00:00Z',
                        'payload':{'type':'message','role':'assistant','phase':'final',
                                   'content':[{'type':'output_text','text':'\\\n'*1000}]}})+'\n')
                stream.write(json.dumps({'type':'response_item','timestamp':'2026-01-03T00:00:00Z',
                    'payload':{'type':'message','role':'assistant','phase':'analysis',
                               'content':[{'type':'output_text','text':'PRIVATE analysis'}]}})+'\n')
            result=history_snapshot(ID,home=home,message_limit=20,max_chars=2000)
            self.assertEqual(result['snapshotAsOf'],'2026-01-02T00:00:00Z')
            self.assertNotIn('PRIVATE',str(result))
            self.assertLess(len(json.dumps(result,ensure_ascii=False)),24000)
            self.assertTrue(result['partialHistory'])


if __name__=='__main__':
    unittest.main()
