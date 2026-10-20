import unittest
from unittest.mock import Mock,patch
from backend import Bridge


class ProcessOutputTests(unittest.TestCase):
    def test_single_large_line_is_readable_in_redacted_character_pages(self):
        native=Mock(return_value={'status':'exited','output':'prefix API_KEY=sk-'+'a'*40+' end '+'字'*20000,'total_lines':1})
        b=Bridge(native=native)
        self.addCleanup(b.close)
        session=Mock(owner_task_id=b.owner)
        with patch('backend.process_registry.get',return_value=session):
            r=b.call('hermes_process_read',{'action':'log','session_id':'owned','offset':0,'max_chars':1000})
            self.assertEqual(len(r['output']),1000)
            self.assertNotIn('sk-',r['output'])
            self.assertEqual(r['log_page']['next_char_offset'],1000)
            s=b.call('hermes_process_read',{'action':'log','session_id':'owned','offset':0,'char_offset':1000,'max_chars':1000})
            self.assertEqual(s['log_page']['char_offset'],1000)
            self.assertEqual(s['log_page']['offset'],0)
            self.assertEqual(s['log_page']['max_chars'],1000)
            self.assertEqual(s['log_page']['content_id'],r['log_page']['content_id'])
            self.assertNotIn('char_offset',native.call_args.args[1])
        with self.assertRaises(ValueError):
            b.call('hermes_process_read',{'action':'poll','session_id':'owned','char_offset':1})


if __name__=='__main__':
    unittest.main()
