import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock, patch
from backend import Bridge

ID='11111111-1111-4111-8111-111111111111'


class TrackingIntegrationTests(unittest.TestCase):
    def test_start_returns_first_baseline_and_status_accepts_bounded_wait(self):
        b=Bridge(codex=lambda *_:{'thread':{'id':ID,'status':{'type':'active'}},
                                 'latestTurn':{'id':'turn1','status':'inProgress'}})
        self.addCleanup(b.close)
        r=b.call('hermes_tracking',{'action':'start','kind':'codex','thread_id':ID})
        self.assertIsNotNone(r['current_state'],'start must wait for the first bounded baseline')
        self.assertTrue(r['baseline_ready'])
        cursor=r['latest_sequence']
        s=b.call('hermes_tracking_status',{'tracking_id':r['tracking_id'],'after_sequence':cursor,'wait_seconds':0.01})
        self.assertEqual(s['wait']['reason'],'timeout')
        self.assertTrue(s['alive'])
        for value in (True,26,-1):
            with self.assertRaises(ValueError):
                b.call('hermes_tracking_status',{'tracking_id':r['tracking_id'],'wait_seconds':value})

    def test_status_wait_releases_bridge_lock_for_other_requests(self):
        b=Bridge()
        self.addCleanup(b.close)
        entered,release=threading.Event(),threading.Event()
        def wait(*_,**kw):
            entered.set();release.wait(3);return {'alive':True}
        b.tracking.status=Mock(side_effect=wait)
        with ThreadPoolExecutor() as pool:
            future=pool.submit(b.call,'hermes_tracking_status',{'tracking_id':'a'*32,'wait_seconds':1})
            self.assertTrue(entered.wait(1))
            try:
                with patch('discovery.context',return_value={'workflow':{'required_skill_ids':[]}}):
                    result=b.call('hermes_context',{})
                self.assertEqual(result.get('bootstrap',{}).get('status'),'ready')
            finally:
                release.set()
            self.assertTrue(future.result()['alive'])


if __name__=='__main__':
    unittest.main()
