import unittest
from backend import safe_result
from browser_adapter import BrowserAdapter


class OutputTests(unittest.TestCase):
    def test_outer_cap_preserves_error_semantics_and_original_size(self):
        r=safe_result({'error':'IMPORTANT_ERROR','errorCode':'FIXTURE_FAILURE','stage':'execute',
            'status':'exited','exit_code':1,'session_id':'owned','output_cut':123,'output':'x'*100000})
        self.assertIn('error',r,'truncation must preserve structured error semantics')
        self.assertEqual(r['error'],'IMPORTANT_ERROR')
        self.assertEqual(r['errorCode'],'FIXTURE_FAILURE')
        self.assertEqual(r['stage'],'execute')
        self.assertEqual(r['session_id'],'owned')
        self.assertEqual(r['output_cut'],123)
        self.assertTrue(r['truncated'])
        self.assertGreater(r['truncation']['original_chars'],100000)

    def test_browser_receipt_omits_wrapper_and_exposes_retained_log_truncation(self):
        b=BrowserAdapter()
        b.sessions={'owned':{'prepare_ms':1,'launch_ms':2,'wait_ms':3}}
        r=b.collect('owned',{'session_id':'owned','status':'exited','exit_code':0,
            'command':'LONG_POWERSHELL_WRAPPER','output':'tail'*1000,'output_cut':15450})
        self.assertIsNotNone(r,'completed browser sessions still need formatted log receipts')
        self.assertNotIn('command',r.payload)
        self.assertEqual(r.payload['observation']['original_chars'],19450)
        self.assertTrue(r.payload['observation']['truncated'])
        self.assertEqual(r.payload['observation']['length_scope'],'retained_process_buffer')
        self.assertEqual(r.payload['log']['tool'],'hermes_process_read')
        self.assertEqual(r.payload['timings_ms']['prepare_ms'],1)

    def test_large_error_metadata_stays_bounded_without_disappearing(self):
        import json
        r=safe_result({'error':'X'*100000,'exit_code':1,'output':'y'*100000})
        self.assertIn('error',r,'large error text must not erase the error flag')
        self.assertTrue(r['error'])
        self.assertLess(len(json.dumps(r)),65000)

    def test_browser_log_page_preserves_partial_flag_and_selected_size(self):
        from browser_output import format_result
        result = format_result({'output': 'x'*1000, 'truncated': True,
            'log_page': {'total_chars': 9000, 'next_char_offset': 1000}}, 'owned', {})
        self.assertTrue(result['truncated'])
        self.assertEqual(result['observation']['original_chars'], 9000)
        self.assertEqual(result['observation']['length_scope'], 'selected_retained_lines')

    def test_final_serialized_envelope_bounds_escaped_output(self):
        import json
        result = safe_result({'error': 'fixture', 'output': '\\"'*100000})
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), 64000)

    def test_browser_log_receipt_keeps_actual_selected_page_arguments(self):
        from browser_output import format_result
        result = format_result({'output': 'slice', 'log_page': {'total_chars': 100,
            'next_char_offset': 60, 'offset': 400, 'limit': 8, 'char_offset': 55, 'max_chars':5}}, 'owned', {})
        self.assertEqual(result['log']['arguments'], {'action':'log', 'session_id':'owned',
            'offset':400, 'limit':8, 'char_offset':55, 'max_chars':5})


if __name__=='__main__':
    unittest.main()
