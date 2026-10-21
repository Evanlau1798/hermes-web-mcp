import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from backend import Bridge
from browser_adapter import BrowserAdapter
import discovery
import runtime


class BrowserReceiptTests(unittest.TestCase):
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
