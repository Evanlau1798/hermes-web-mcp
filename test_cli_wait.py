import unittest
from unittest.mock import Mock, patch


class CliWaitTests(unittest.TestCase):
    def test_cli_keeps_original_worker_alive_until_delivery_receipt(self):
        from codex_adapter import wait_cli_result
        bridge=Mock()
        bridge.call.side_effect=[{'operation_id':'test-op-001','state':'compacting','worker_alive':True},
                                 {'operation_id':'test-op-001','state':'delivery_accepted','worker_alive':False}]
        with patch('time.sleep'):
            result=wait_cli_result(bridge,{'operation_id':'test-op-001','state':'compacting','worker_alive':True})
        self.assertEqual(result['state'],'delivery_accepted')
        self.assertEqual(bridge.call.call_count,2)
        bridge.close.assert_not_called()


if __name__=='__main__': unittest.main()
