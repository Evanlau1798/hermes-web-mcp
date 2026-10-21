import copy
import tempfile
import unittest
from unittest.mock import Mock
import runtime

ID = '11111111-1111-4111-8111-111111111111'


def snapshot(state='idle', backend='daemon'):
    return {'thread': {'id': ID, 'title': 'Exact title', 'status': {'type': state}},
            'latestTurn': {'id': 'baseline', 'status': 'completed', 'completedAt': 1700000000},
            'execution_backend': backend, 'backend_instance_id': backend+'-instance',
            'compactState': {'lastTurnIsCompaction': False, 'nativeCompactionItemSeen': False},
            'contextUsage': {'contextTokens': None}}


class RouterTests(unittest.TestCase):
    def test_status_during_job_creation_is_not_a_false_receipt_conflict(self):
        import threading
        from send_coordinator import SendCoordinator
        from test_compact_jobs import snapshot as native_snapshot
        with tempfile.TemporaryDirectory() as folder:
            entered,release=threading.Event(),threading.Event()
            def api(action,args):
                return native_snapshot(thread_id=ID) if action=='status' else {'deliveryState':'accepted'}
            manager=SendCoordinator(api,root=folder)
            jobs=manager._jobs(); original=jobs.prepare
            def prepare(*args,**kwargs):
                result=original(*args,**kwargs); entered.set(); release.wait(2); return result
            jobs.prepare=prepare
            worker=threading.Thread(target=lambda:manager.submit(ID,'Exact title','operation-race-1','private'))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                self.assertNotEqual(manager.status('operation-race-1').get('errorCode'),'RECEIPT_CONFLICT')
            finally:
                release.set(); worker.join(3); manager.close()

    def router(self, desktop_state='notLoaded', daemon_loaded=True):
        from codex_router import CodexRouter
        desktop = Mock(return_value=snapshot(desktop_state, 'desktop'))
        daemon = Mock()
        daemon.probe.return_value = {'available': True, 'loaded': daemon_loaded,
                                     'instance_id': 'daemon-instance'}
        daemon.return_value = snapshot()
        return CodexRouter(desktop, daemon), desktop, daemon

    def test_read_uses_daemon_without_loading_and_conflicting_owners_refuse(self):
        router, desktop, daemon = self.router()
        self.assertEqual(router('status', {'thread_id': ID})['execution_backend'], 'daemon')
        self.assertEqual(daemon.call_args.args[0], 'status')
        router, desktop, daemon = self.router('idle')
        self.assertEqual(router('status', {'thread_id': ID})['errorCode'], 'BACKEND_OWNER_CONFLICT')
        daemon.assert_not_called()

    def test_cold_read_does_not_load_but_explicit_ensure_loads_exact_target(self):
        router, desktop, daemon = self.router(daemon_loaded=False)
        self.assertEqual(router('status', {'thread_id': ID})['thread']['status']['type'], 'notLoaded')
        daemon.assert_not_called()
        result = router('ensure', {'thread_id': ID, 'expected_title': 'Exact title'})
        self.assertEqual(result.get('execution_backend'), 'daemon')
        self.assertEqual(daemon.call_args.args[0], 'load')

    def test_pinned_operation_never_switches_after_daemon_restarts(self):
        router, desktop, daemon = self.router()
        result = router('send', {'thread_id': ID, 'execution_backend': 'daemon',
            'backend_instance_id': 'old-instance', 'message': 'private'})
        self.assertEqual(result['errorCode'], 'BACKEND_INSTANCE_CHANGED')
        self.assertEqual(result['deliveryState'], 'not-sent')
        desktop.assert_not_called()
        daemon.assert_not_called()

    def test_desktop_stays_available_when_daemon_is_absent(self):
        router, desktop, daemon = self.router('idle', False)
        daemon.probe.return_value = {'available': False, 'errorCode': 'DAEMON_UNAVAILABLE'}
        self.assertEqual(router('status', {'thread_id': ID})['execution_backend'], 'desktop')

    def test_coordinator_ensures_cold_target_and_pins_idle_worker_receipts(self):
        from send_coordinator import SendCoordinator
        from test_compact_jobs import snapshot as native_snapshot, wait_for
        data = native_snapshot(thread_id=ID)
        data.update(execution_backend='daemon',backend_instance_id='daemon-instance')
        calls = []
        def api(action, args):
            calls.append((action,dict(args)))
            if action in ('status','ensure'):
                return copy.deepcopy(data)
            if action=='send':
                return {'deliveryState':'accepted','native_turn_id':'new-native-turn',
                    'execution_backend':'daemon','backend_instance_id':'daemon-instance'}
            raise AssertionError(action)
        with tempfile.TemporaryDirectory() as folder:
            manager = SendCoordinator(api,root=folder)
            manager.submit(ID,'Exact title','operation-daemon-1','PRIVATE')
            self.assertTrue(wait_for(lambda:manager.status('operation-daemon-1')['summary']['delivery']=='accepted'))
            receipt = manager.status('operation-daemon-1')
            self.assertEqual(receipt.get('execution_backend'),'daemon')
            self.assertEqual(receipt.get('native_turn_id'),'new-native-turn')
            for action,args in calls:
                if action=='send':
                    self.assertEqual(args.get('execution_backend'),'daemon')
                    self.assertEqual(args.get('backend_instance_id'),'daemon-instance')
            manager.close()


class AdapterTests(unittest.TestCase):
    def adapter(self, replies):
        from daemon_adapter import DaemonAdapter
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.request.side_effect = lambda method, params: copy.deepcopy(replies[method])
        adapter = DaemonAdapter(client_factory=lambda: client, identity=lambda: 'daemon-instance')
        return adapter, client

    def replies(self):
        return {'thread/read': {'thread': {'id': ID, 'name': 'Exact title', 'model': 'test-model',
            'cwd': 'test-cwd', 'status': {'type': 'idle'}}},
            'thread/loaded/list': {'data': [ID]},
            'thread/turns/list': {'data': [{'id': 'baseline', 'status': 'completed',
                'completedAt': 1700000000, 'items': [{'type': 'agentMessage', 'text': 'safe'}]}]},
            'thread/resume': {'thread': {'id': ID, 'status': {'type': 'idle'}},
                'model': 'test-model', 'cwd': 'test-cwd'},
            'turn/start': {'turn': {'id': 'new-turn'}}, 'turn/steer': {'turnId': 'baseline'}}

    def test_active_uses_native_steer_with_expected_turn_without_overrides(self):
        replies = self.replies()
        replies['thread/read']['thread']['status']['type'] = 'active'
        replies['thread/turns/list']['data'][0]['status'] = 'inProgress'
        adapter, client = self.adapter(replies)
        result = adapter('send', {'thread_id': ID, 'expected_title': 'Exact title',
            'message': 'private', 'expected_baseline_turn_id': 'baseline', 'active_baseline': True})
        self.assertEqual(result.get('native_turn_id'), 'baseline')
        method, args = client.request.call_args.args
        self.assertEqual(method, 'turn/steer')
        self.assertEqual(args['expectedTurnId'], 'baseline')
        for key in ('model', 'approvalPolicy', 'sandboxPolicy', 'cwd'):
            self.assertNotIn(key, args)

    def test_cold_load_rejects_unknown_settings_and_never_sends(self):
        replies = self.replies()
        replies['thread/loaded/list']['data'] = []
        adapter, client = self.adapter(replies)
        adapter.resume_settings = lambda _: None
        result = adapter('load', {'thread_id': ID, 'expected_title': 'Exact title'})
        self.assertEqual(result.get('errorCode'), 'RESUME_SETTINGS_UNCONFIRMED')
        self.assertFalse(any(c.args[0] in ('thread/resume', 'turn/start') for c in client.request.call_args_list))

    def test_daemon_snapshot_is_bounded_and_contains_native_compact_state(self):
        adapter, client = self.adapter(self.replies())
        result = adapter('status', {'thread_id': ID})
        self.assertEqual(result.get('latestTurn', {}).get('id'), 'baseline')
        self.assertFalse(result['compactState']['lastTurnIsCompaction'])
        self.assertEqual(result['execution_backend'], 'daemon')
        args = next(c.args[1] for c in client.request.call_args_list if c.args[0]=='thread/turns/list')
        self.assertEqual(args['limit'], 2)

    def test_operation_observation_can_find_its_exact_turn_after_a_newer_turn(self):
        replies=self.replies(); replies['thread/turns/list']['data'].insert(0,{
            'id':'newer-turn','status':'inProgress','items':[]})
        adapter,client=self.adapter(replies)
        result=adapter('status',{'thread_id':ID,'native_turn_id':'baseline'})
        self.assertEqual(result.get('operationTurn',{}).get('id'),'baseline')
        self.assertEqual(result['latestTurn']['id'],'newer-turn')


if __name__ == '__main__':
    unittest.main()
