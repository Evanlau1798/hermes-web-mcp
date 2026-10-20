import unittest
from backend import safe_result


class SafetyTests(unittest.TestCase):
    def test_redaction_preserves_structure_and_masks_plain_secret_fields(self):
        result = safe_result({'text': 'API_KEY=sk-abcdefghijklmnopqrstuvwx', 'password': 'plain-password',
                              'nested': [{'ok': True}]})
        self.assertNotIn('sk-abcdefghijklmnopqrstuvwx', str(result))
        self.assertNotIn('plain-password', str(result))
        self.assertEqual(result['nested'], [{'ok': True}])

    def test_output_cap_keeps_page_cursor_and_execution_state(self):
        result = safe_result({'turns': [{'text': 'x' * 90000}], 'page': {'nextCursor': 'native-cursor'},
                              'executionComplete': False, 'deliveryState': 'accepted'})
        self.assertTrue(result['truncated'])
        self.assertEqual(result['page']['nextCursor'], 'native-cursor')
        self.assertFalse(result['executionComplete'])


if __name__ == '__main__':
    unittest.main()
