import tempfile
import time
import unittest
from compact_jobs import CompactJobs
from test_compact_jobs import snapshot,FakeAPI,THREAD_ID,wait_for


class OperationSummaryTests(unittest.TestCase):
    def test_skipped_compact_and_accepted_delivery_do_not_claim_task_completion(self):
        before=snapshot(completed_at=time.time()-4000)
        before['contextUsage']={'contextTokens':None}
        root=self.enterContext(tempfile.TemporaryDirectory())
        api=FakeAPI([before]*4)
        manager=CompactJobs(api,root=root)
        self.addCleanup(manager.close)
        manager.prepare(THREAD_ID,'Exact title','message','operation-summary')
        manager.confirm('operation-summary','auto')
        self.assertTrue(wait_for(lambda:manager.status('operation-summary')['state']=='delivery_accepted'))
        result=manager.status('operation-summary')
        self.assertIn('summary',result)
        self.assertEqual(result['summary']['compact'],'not_required')
        self.assertEqual(result['summary']['delivery'],'accepted')
        self.assertEqual(result['summary']['turn'],'unobserved')
        self.assertEqual(result['summary']['task_completion'],'not_established')
        api.statuses=[snapshot(turn_id='turn-new',turn_state='inProgress',thread_state='active')]
        observed=manager.status('operation-summary',observe_turn=True)
        self.assertEqual(observed['summary']['turn'],'started')
        self.assertEqual(observed['turn_observation']['correlation'],'newer_than_baseline_only')
        self.assertEqual(sum(action=='send' for action,_ in api.calls),1)
        for next_turn,thread_state,expected in [('turn0','idle','unobserved'),('turn-new','notLoaded','unknown'),
                                                ('turn-new','idle','complete')]:
            api.statuses=[snapshot(turn_id=next_turn,thread_state=thread_state)]
            observed=manager.status('operation-summary',observe_turn=True)
            self.assertEqual(observed['summary']['turn'],expected)
            self.assertEqual(observed['summary']['task_completion'],'not_established')


if __name__=='__main__':
    unittest.main()
