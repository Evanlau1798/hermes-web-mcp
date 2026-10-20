import base64
import unittest
import time
from unittest.mock import patch

from backend import Bridge


PNG = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/l9sAAAAASUVORK5CYII='


class ComputerResultTests(unittest.TestCase):
    def test_single_computer_tool_is_discoverable(self):
        self.assertTrue('hermes_computer_use' in Bridge().specs)

    def test_bridge_namespaces_and_releases_expired_computer_sessions(self):
        class FakeComputer:
            def __init__(self):
                self.calls, self.closed = [], []
            def computer_use(self, arguments, *, session_id):
                self.calls.append(session_id)
                return '{"ok":true}'
            def close(self, session_id=None):
                self.closed.append(session_id)
        computer = FakeComputer()
        bridge = Bridge(computer=computer)
        self.assertEqual(bridge.call('hermes_computer_use', {'action': 'capture'}, client_scope='a')['errorCode'], 'bootstrap_required')
        for scope in ('a', 'b'):
            bridge.call('hermes_context', {}, client_scope=scope)
            bridge.call('hermes_computer_use', {'action': 'capture'}, client_scope=scope)
        self.assertNotEqual(*computer.calls)
        bridge.clients['a']['seen'] = time.monotonic() - 28801
        bridge.call('hermes_context', {}, client_scope='b')
        self.assertIn(computer.calls[0], computer.closed)
        bridge.close()
        self.assertIn(None, computer.closed)

    def test_images_travel_separately_from_redacted_text(self):
        from computer_result import normalize
        raw = {'_multimodal': True, 'text_summary': 'Capture', 'meta': {'width': 1, 'height': 1},
               'content': [{'type': 'text', 'text': 'API_KEY=sk-abcdefghijklmnopqrstuvwx'},
                           {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + PNG}}]}
        result = normalize(raw)
        self.assertNotIn('sk-abcdefghijklmnopqrstuvwx', str(result.payload))
        self.assertNotIn(PNG, str(result.payload))
        self.assertEqual(result.images, [('image/png', PNG)])
        self.assertEqual(result.payload['image_count'], 1)
        for url in ('https://example.com/image.png', 'data:image/svg+xml;base64,' + PNG,
                    'data:image/png;base64,' + base64.b64encode(b'not an image').decode()):
            raw['content'][1]['image_url']['url'] = url
            with self.assertRaises(ValueError):
                normalize(raw)


if __name__ == '__main__':
    unittest.main()
