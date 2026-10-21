import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
import runtime
import psutil
from test_compact_jobs import FakeAPI, CompactJobs, snapshot, THREAD_ID, wait_for


class ReviewRegressions(unittest.TestCase):
    def test_interrupted_baseline_can_send_but_is_not_confirmed_compaction(self):
        before=snapshot(turn_state='interrupted')
        api=FakeAPI([before]*4)
        with tempfile.TemporaryDirectory() as folder:
            manager=CompactJobs(api,root=folder)
            try:
                prepared=manager.prepare(THREAD_ID,'Exact title','new instruction','stop-resume-1')
                self.assertEqual(prepared['state'],'awaiting_confirmation')
                manager.confirm('stop-resume-1','auto')
                self.assertTrue(wait_for(lambda:manager.status('stop-resume-1')['state']=='delivery_accepted'))
                self.assertFalse(manager.status('stop-resume-1')['compact_complete'])
                self.assertFalse(any(a=='compact_start' for a,_ in api.calls))
            finally:manager.close()

    def test_foreign_live_owner_does_not_become_not_sent_and_dead_preparing_terminates(self):
        from send_coordinator import SendCoordinator
        with tempfile.TemporaryDirectory() as folder:
            manager=SendCoordinator(Mock(),root=folder)
            manager.store.reserve('loading-owner-1',THREAD_ID)
            manager.store.save({'operation_id':'loading-owner-1','thread_id':THREAD_ID,'route':'loading',
                'state':'loading','deliveryState':'not_started','owner_pid':999999,'owner_started_at':12.0})
            with patch('psutil.Process',return_value=Mock(create_time=Mock(return_value=12.0))):
                self.assertEqual(manager.status('loading-owner-1')['state'],'loading')
            manager.store.reserve('preparing-owner-1',THREAD_ID)
            manager.store.save({'operation_id':'preparing-owner-1','thread_id':THREAD_ID,'route':'idle',
                'state':'preparing','deliveryState':'not_started','owner_pid':999999,'owner_started_at':12.0})
            with patch('psutil.Process',side_effect=psutil.NoSuchProcess(999999)):
                self.assertEqual(manager.status('preparing-owner-1')['state'],'interrupted')

    def test_cold_status_publishes_a_fixed_load_candidate_instance(self):
        from test_daemon_route import RouterTests, ID
        router,desktop,daemon=RouterTests().router(daemon_loaded=False)
        result=router('status',{'thread_id':ID})
        self.assertEqual(result.get('load_binding',{}).get('backend_instance_id'),'daemon-instance')
        daemon.probe.return_value['instance_id']='new-instance'
        result=router.ensure_loaded(ID,'Exact title',execution_backend='daemon',backend_instance_id='daemon-instance')
        self.assertEqual(result['errorCode'],'BACKEND_INSTANCE_CHANGED')
        daemon.assert_not_called()

    def test_pinned_desktop_tracker_does_not_accept_an_unloaded_owner(self):
        from test_daemon_route import RouterTests, ID
        router,desktop,daemon=RouterTests().router(daemon_loaded=False)
        result=router('status',{'thread_id':ID,'execution_backend':'desktop','backend_instance_id':'desktop-instance'})
        self.assertEqual(result.get('errorCode'),'BACKEND_OWNER_CHANGED')

    def test_unreadable_identity_is_not_reported_as_daemon_absent(self):
        from daemon_rpc import identity, DaemonError
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);(root/'app-server-daemon').mkdir()
            (root/'app-server-daemon/daemon.pid').write_text(json.dumps({'pid':123,'processStartTime':'1'}))
            with patch('daemon_rpc.home',return_value=root),patch('psutil.Process',side_effect=psutil.AccessDenied(123)):
                with self.assertRaises(DaemonError) as caught:identity()
                self.assertEqual(caught.exception.code,'DAEMON_IDENTITY_UNCONFIRMED')

    def test_active_dispatch_crash_remains_unknown_after_its_owner_exits(self):
        from send_coordinator import SendCoordinator
        def api(action,args):
            if action=='status':return snapshot(thread_state='active')
            raise SystemExit('simulated sender process loss')
        with tempfile.TemporaryDirectory() as folder:
            manager=SendCoordinator(api,root=folder)
            with self.assertRaises(SystemExit):
                manager.submit(THREAD_ID,'Exact title','active-crash-1','private')
            with patch('send_coordinator.owner_alive',return_value=False):
                result=manager.status('active-crash-1')
            self.assertEqual(result['state'],'delivery_unknown')
            self.assertEqual(result['summary']['delivery'],'unknown')

    def test_tracking_preserves_the_first_backend_binding(self):
        from feature_tools import track
        bridge=Mock();calls=[]
        def sample(action,args):
            calls.append(dict(args))
            return {'thread':{'id':THREAD_ID,'status':{'type':'active'}},
                'latestTurn':{'id':'one','status':'inProgress'},
                'execution_backend':'daemon','backend_instance_id':'instance-one'}
        bridge.codex=sample
        def start(*args,**kw):
            sampler=kw.get('sampler') or args[2]
            sampler();return sampler()
        bridge.tracking.start.side_effect=start
        track(bridge,{'action':'start','kind':'codex','thread_id':THREAD_ID})
        self.assertEqual(calls[1].get('backend_instance_id'),'instance-one')


if __name__=='__main__':unittest.main()
