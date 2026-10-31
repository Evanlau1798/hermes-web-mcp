import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import Bridge
from browser_adapter import BrowserAdapter
import discovery
import runtime


class BrowserReceiptTests(unittest.TestCase):
    def test_abnormal_exit_does_not_promise_completion_or_loop_polling(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = BrowserAdapter(Path(directory))
            folder = Path(directory)/'round'
            folder.mkdir()
            (folder/'screenshot.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            adapter.runs['owned'] = folder
            adapter.sessions['owned'] = {}
            for action in ('kill','log','poll'):
                with self.subTest(action=action):
                    result = adapter.collect('owned', {'status':'exited','exit_code':-1,
                        'completion_reason':'killed','output':'last'}, deliver_images=action=='poll')
                    self.assertEqual(result.images, [])
                    self.assertEqual(result.payload['image_delivery']['state'], 'outcome_unknown')
                    self.assertNotIn('next_action', result.payload)
                    self.assertTrue(folder.exists(), 'CLI exit cannot authorize deleting a live script')
                    self.assertFalse(result.payload['automaticRetrySafe'])

    def test_text_log_never_consumes_pending_image_or_cleans_round(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = BrowserAdapter(Path(directory))
            folder = Path(directory)/'round'
            folder.mkdir()
            (folder/'screenshot.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            adapter.runs['owned'] = folder
            adapter.sessions['owned'] = {}
            logged = adapter.collect('owned', {'status':'exited','output':'text'}, deliver_images=False)
            self.assertEqual(logged.images, [])
            self.assertTrue(folder.exists())
            self.assertEqual(logged.payload['image_delivery']['state'], 'pending_completion_receipt')
            self.assertEqual(logged.payload['next_action']['arguments'], {'action':'poll','session_id':'owned'})
            done = adapter.collect('owned', {'status':'exited','exit_code':0})
            self.assertEqual(len(done.images), 1)
            self.assertEqual(done.payload['image_delivery'], {'state':'attached',
                'host_display':'unverified','visual_verification':'unverified'})
            again = adapter.collect('owned', {'status':'exited','exit_code':0})
            self.assertEqual(again.images, [])
            self.assertEqual(again.payload['image_delivery']['state'], 'delivered_previously')

    def test_bridge_log_dispatch_keeps_image_for_completion_poll(self):
        import threading
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as directory:
            native = Mock(side_effect=[{'output':'text','total_lines':1},
                {'status':'exited','output':'text','exit_code':0}])
            bridge = Bridge(native=native)
            self.addCleanup(bridge.close)
            bridge.browser = BrowserAdapter(Path(directory))
            folder = Path(directory)/'round'
            folder.mkdir()
            (folder/'screenshot.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            bridge.browser.runs['owned'] = folder
            bridge.browser.sessions['owned'] = {}
            session = SimpleNamespace(owner_task_id=bridge.owner, _lock=threading.Lock(),
                total_output_chars=4, output_buffer='text')
            with patch('backend.process_registry.get', return_value=session):
                log = bridge.call('hermes_process_read', {'action':'log','session_id':'owned'})
                self.assertEqual(log.images, [])
                self.assertEqual(log.payload['next_action']['arguments']['action'], 'poll')
                done = bridge.call('hermes_process_read', {'action':'poll','session_id':'owned'})
            self.assertEqual(len(done.images), 1)
            self.assertEqual(native.call_count, 2)

    def test_evicted_native_buffer_is_never_reported_as_complete_history(self):
        import threading
        from types import SimpleNamespace
        adapter=BrowserAdapter()
        adapter.sessions['owned']={}
        session=SimpleNamespace(_lock=threading.Lock(),total_output_chars=250000,output_buffer='x'*200000)
        with patch('backend.process_registry.get',return_value=session):
            result=adapter.collect('owned',{'status':'exited','output':'kept text','exit_code':0})
        self.assertEqual(result.payload['output_history']['evicted_raw_chars'],50000)
        self.assertTrue(result.payload['partial_output'])
        self.assertFalse(result.payload['output_history']['complete_history_available'])

    def test_long_line_continuation_eventually_moves_to_next_line_batch_and_stops(self):
        from process_output import page_log
        from browser_output import format_result
        raw = {'status':'exited', 'output':'x'*6500+'\n'*199, 'total_lines':201, 'showing':'200 lines'}
        first = format_result(page_log(raw, offset=0, limit=200), 'owned', {})
        self.assertEqual(first['next_action']['arguments']['char_offset'],6000)
        tail = format_result(page_log(raw, offset=0, limit=200, char_offset=6000), 'owned', {})
        self.assertEqual(tail['next_action']['arguments']['offset'],200)
        self.assertEqual(tail['next_action']['arguments']['char_offset'],0)
        last = format_result(page_log({'output':'last','total_lines':201}, offset=200), 'owned', {})
        self.assertNotIn('next_action',last)

    def test_blank_native_selected_lines_keep_exact_line_cursor(self):
        from process_output import page_log
        for output, offset, limit, expected in [('data\n',0,2,2), ('',1,1,2)]:
            with self.subTest(output=output):
                page = page_log({'output':output,'total_lines':3,
                    'showing':f'{limit} lines'},offset=offset,limit=limit)
                self.assertEqual(page['log_page']['next_line_offset'],expected)

    def test_head_redaction_precedes_character_pagination(self):
        adapter = BrowserAdapter()
        adapter.sessions['owned'] = {}
        read = lambda _: {'output':'HEAD API_KEY=sk-'+'a'*40+' 中文 '+'x'*8000, 'total_lines':1}
        r=adapter.collect('owned', {'status':'exited','exit_code':0,'output_cut':6000,'output':'tail'}, read_log=read)
        self.assertNotIn('sk-',r.payload['output'])
        self.assertIn('API_KEY=***',r.payload['output'])

    def test_completed_tail_is_replaced_by_redacted_head_page_without_replaying(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = BrowserAdapter(Path(directory))
            adapter.sessions['owned'] = {}
            text = 'HEAD 繁體中文\n' + '字' * 8000
            read = Mock(return_value={'status': 'exited', 'output': text, 'total_lines': 2})
            result = adapter.collect('owned', {'status': 'exited', 'exit_code': 7,
                'output': text[-2000:], 'output_cut': len(text)-2000}, read_log=read)
            self.assertTrue(result.payload['output'].startswith('HEAD 繁體中文'))
            self.assertEqual(len(result.payload['output']), 6000)
            self.assertEqual(result.payload['exit_code'], 7)
            self.assertTrue(result.payload['partial_output'])
            self.assertEqual(result.payload['next_action']['arguments']['char_offset'], 6000)
            read.assert_called_once_with({'action': 'log', 'session_id': 'owned', 'offset': 0, 'limit': 200})

    def test_missing_retained_log_keeps_exit_and_marks_unavailable(self):
        adapter = BrowserAdapter()
        adapter.sessions['owned'] = {}
        result = adapter.collect('owned', {'status': 'exited', 'exit_code': 0,
            'output': 'tail', 'output_cut': 9000}, read_log=lambda _: {'status': 'not_found'})
        self.assertEqual(result.payload['status'], 'exited')
        self.assertEqual(result.payload['exit_code'], 0)
        self.assertEqual(result.payload['log_availability'], 'unavailable')
        self.assertFalse(result.payload['automaticRetrySafe'])

    def test_poll_completion_enriches_once_and_does_not_repeat_image(self):
        with tempfile.TemporaryDirectory() as directory:
            adapter = BrowserAdapter(Path(directory))
            folder = Path(directory)/'round'
            folder.mkdir()
            (folder/'screenshot.png').write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            adapter.runs['owned'] = folder
            adapter.sessions['owned'] = {}
            read = Mock(return_value={'output': 'FIRST '+ 'x'*8000, 'total_lines': 1})
            active = adapter.collect('owned', {'status':'running','output_preview':'tail'}, read_log=read)
            read.assert_not_called()
            self.assertEqual(active.payload['next_action']['arguments']['action'], 'poll')
            done = adapter.collect('owned', {'status':'exited','exit_code':0,'output_preview':'tail'}, read_log=read)
            self.assertEqual(len(done.images), 1)
            again = adapter.collect('owned', {'status':'exited','exit_code':0,'output_preview':'tail'}, read_log=read)
            self.assertEqual(again.images, [])
            read.assert_called_once()

    def test_reading_metrics_measure_reads_without_retaining_text_and_reset_by_task(self):
        bridge = Bridge(native=Mock())
        self.addCleanup(bridge.close)
        with patch.object(discovery, 'SKILLS_ROOT', runtime.ROOT/'skills'), patch.object(discovery, 'CODEX_ROOTS', []):
            start = bridge.call('hermes_context', {'task':'browser use'}, client_scope='metrics')
            self.assertEqual(start['reading_metrics']['read_calls'], 1)
            args = start['browser_use']['guide']
            first = bridge.call('hermes_skill_read', args, client_scope='metrics')
            second = bridge.call('hermes_skill_read', args, client_scope='metrics')
            self.assertEqual(second['reading_metrics']['read_calls'], 3)
            self.assertEqual(second['reading_metrics']['repeated_reads'], 1)
            self.assertEqual(second['reading_metrics']['returned_chars'],
                start['reading_metrics']['returned_chars'] + len(first['content']) * 2)
            self.assertNotIn(first['content'], str(bridge.clients['metrics']['reading_metrics']))
            reset = bridge.call('hermes_context', {'task':'different task'}, client_scope='metrics')
            self.assertEqual(reset['reading_metrics']['repeated_reads'], 0)


if __name__ == '__main__':
    unittest.main()
