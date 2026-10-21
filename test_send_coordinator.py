import json
import sqlite3
import tempfile
import time
import threading
import unittest
import runtime
from pathlib import Path
from unittest.mock import Mock
from test_compact_jobs import snapshot, THREAD_ID, wait_for


class SendCoordinatorTests(unittest.TestCase):
    def make(self, folder, state='idle'):
        from send_coordinator import SendCoordinator
        data=snapshot(thread_state=state, completed_at=time.time()-2000)
        data['contextUsage']={'contextTokens':None}
        def call(action,args):
            if action=='status': return data
            if action=='delivery_status': return {'errorCode':'OPERATION_NOT_FOUND'}
            if action=='send': return {'deliveryState':'accepted','threadId':THREAD_ID}
            raise AssertionError(action)
        api=Mock(side_effect=call)
        manager=SendCoordinator(api,root=folder)
        self.addCleanup(manager.close)
        return manager,api,data

    def args(self, operation='send-test-0001'):
        return dict(thread_id=THREAD_ID,expected_title='Exact title',message='PRIVATE MESSAGE',operation_id=operation)

    def test_all_aliases_share_active_delivery_and_durable_unknown_dedup(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder,'active')
            first=manager.submit(**self.args())
            self.assertEqual(first['summary']['delivery'],'accepted')
            self.assertEqual(first['operation_id'],'send-test-0001')
            self.assertEqual(manager.submit(**self.args())['messageContentVerified'],False)
            self.assertEqual(sum(c.args[0]=='send' for c in api.call_args_list),1)
            self.assertEqual(manager.delivery_status(first['operation_id'])['summary']['delivery'],'accepted')
            self.assertEqual(manager.delivery_status(first['delivery_operation_id'])['operation_id'],first['operation_id'])
            from send_coordinator import SendCoordinator
            restarted=SendCoordinator(api,root=folder)
            self.addCleanup(restarted.close)
            self.assertEqual(restarted.submit(**self.args())['summary']['delivery'],'accepted')
            self.assertEqual(sum(c.args[0]=='send' for c in api.call_args_list),1)

    def test_active_rejection_preserves_native_reason_and_unknown_is_error(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder,'active')
            api.side_effect=lambda action,args: data if action=='status' else {
                'deliveryState':'not-sent','errorCode':'TARGET_CHANGED','error':'native rejection'}
            receipt=manager.submit(**self.args())
            self.assertFalse(receipt.get('ok',True))
            self.assertEqual(receipt.get('error'),'native rejection')

    def test_internal_delivery_id_cannot_be_reused_as_new_public_intent(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder,'active')
            first=manager.submit(**self.args())
            duplicate=manager.submit(**self.args(first['delivery_operation_id']))
            self.assertTrue(duplicate.get('deduplicated'))
            self.assertEqual(sum(c.args[0]=='send' for c in api.call_args_list),1)
            raw=Path(folder,'send-intents.sqlite').read_bytes()
            for secret in ('PRIVATE MESSAGE','Exact title'):
                self.assertNotIn(secret.encode(),raw)

    def test_idle_unknown_context_uses_one_worker_and_both_ids_resolve(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder)
            manager.submit(**self.args())
            self.assertTrue(wait_for(lambda:manager.status('send-test-0001')['summary']['delivery']=='accepted'))
            receipt=manager.status('send-test-0001')
            self.assertNotEqual(receipt['delivery_operation_id'],receipt['operation_id'])
            self.assertEqual(manager.delivery_status(receipt['delivery_operation_id'])['operation_id'],receipt['operation_id'])
            self.assertEqual(sum(c.args[0]=='send' for c in api.call_args_list),1)
            self.assertFalse(any(c.args[0]=='compact_start' for c in api.call_args_list))
            manager.close()

    def test_not_loaded_and_invalid_snapshot_are_not_sent_then_new_call_can_be_accepted(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder,'notLoaded')
            first=manager.submit(**self.args())
            self.assertEqual(first['errorCode'],'BACKGROUND_LOAD_UNAVAILABLE')
            self.assertEqual(first['summary']['delivery'],'not-sent')
            data['thread']['status']['type']='active'
            second=manager.submit(**self.args('send-test-0002'))
            self.assertEqual(second['summary']['delivery'],'accepted')
            self.assertEqual(manager.status('send-test-0001')['errorCode'],'BACKGROUND_LOAD_UNAVAILABLE')
            self.assertEqual(sum(c.args[0]=='send' for c in api.call_args_list),1)

    def test_duplicate_after_unknown_does_not_retry_and_mismatched_target_conflicts(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder,'active')
            api.side_effect=lambda action,args: data if action=='status' else {'deliveryState':'unknown'}
            self.assertEqual(manager.submit(**self.args())['summary']['delivery'],'unknown')
            self.assertEqual(manager.submit(**self.args())['summary']['delivery'],'unknown')
            self.assertEqual(manager.submit(**{**self.args(),'thread_id':'22222222-2222-4222-8222-222222222222'})['errorCode'],'OPERATION_ID_CONFLICT')
            self.assertEqual(sum(c.args[0]=='send' for c in api.call_args_list),1)

    def test_legacy_receipts_and_conflicting_sources_are_read_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder)
            db=sqlite3.connect(Path(folder,'codex-delivery.sqlite'))
            db.execute('CREATE TABLE receipts (id TEXT PRIMARY KEY,binding TEXT,result TEXT)')
            db.execute('INSERT INTO receipts VALUES (?,?,?)',('legacy-0001',json.dumps(['send',THREAD_ID]),json.dumps({'deliveryState':'accepted'})))
            db.commit();db.close()
            before=Path(folder,'codex-delivery.sqlite').read_bytes()
            self.assertEqual(manager.status('legacy-0001')['summary']['delivery'],'accepted')
            self.assertTrue(manager.submit(**self.args('legacy-0001'))['deduplicated'])
            self.assertEqual(Path(folder,'codex-delivery.sqlite').read_bytes(),before)
            self.assertEqual(api.call_count,0)
            manager.store.reserve('legacy-0001',THREAD_ID)
            self.assertEqual(manager.status('legacy-0001')['errorCode'],'RECEIPT_CONFLICT')
            self.assertEqual(manager.status('legacy-0001')['summary']['delivery'],'unknown')

    def test_existing_long_public_ids_work_for_idle_and_native_compact_policy(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder,'active')
            result=manager.submit(**self.args('a'*128))
            self.assertLessEqual(len(result['delivery_operation_id']),128)
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder)
            result=manager.submit(**self.args('a'*128))
            self.assertIsNone(result.get('errorCode'))
            self.assertTrue(wait_for(lambda:manager.status('a'*128)['summary']['delivery']=='accepted'))
            manager.close()

    def test_idle_preflight_rejection_remains_queryable_and_child_reuse_is_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder)
            data['latestTurn']['completedAt']=None
            first=manager.submit(**self.args())
            self.assertEqual(first['errorCode'],'SNAPSHOT_INVALID')
            self.assertEqual(manager.status('send-test-0001')['errorCode'],'SNAPSHOT_INVALID')
            manager.close()
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder,'active')
            api.side_effect=lambda action,args: data if action=='status' else {'deliveryState':'accepted','deduplicated':True}
            self.assertEqual(manager.submit(**self.args())['summary']['delivery'],'unknown')

    def test_another_client_read_does_not_interrupt_original_worker_or_write_receipts(self):
        with tempfile.TemporaryDirectory() as folder:
            manager,api,data=self.make(folder)
            entered,release=threading.Event(),threading.Event()
            def call(action,args):
                if action=='status': return data
                if action=='send':
                    entered.set();release.wait(2)
                    return {'deliveryState':'accepted'}
                raise AssertionError(action)
            api.side_effect=call
            manager.submit(**self.args())
            self.assertTrue(entered.wait(1))
            from send_coordinator import SendCoordinator
            other=SendCoordinator(api,root=folder)
            paths=[Path(folder,name) for name in ('send-intents.sqlite','compact-jobs.sqlite')]
            before=[p.read_bytes() for p in paths]
            self.assertEqual(other.status('send-test-0001')['state'],'sending')
            self.assertEqual([p.read_bytes() for p in paths],before)
            self.assertTrue(other.submit(**self.args())['deduplicated'])
            release.set()
            self.assertTrue(wait_for(lambda:manager.status('send-test-0001')['summary']['delivery']=='accepted'))
            manager.close();other.close()


if __name__=='__main__': unittest.main()
