import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

from test_compact_jobs import snapshot, THREAD_ID, CompactJobs, FakeAPI, FakeClock, wait_for


class AutoCompactTests(unittest.TestCase):
    def test_auto_worker_compacts_only_above_both_thresholds_then_sends_original(self):
        for tokens, age, compacted, expected in ((80001, 1801, False, True), (80000, 1801, False, False),
                (80001, 1800, False, False), (80001, 1801, True, False), (None, 1801, False, False)):
            with self.subTest(tokens=tokens, age=age, compacted=compacted), tempfile.TemporaryDirectory() as folder:
                before = snapshot(completed_at=time.time()-age, compacted=compacted)
                before['contextUsage'] = {'contextTokens': tokens}
                after = snapshot(turn_id='turn1', compacted=True)
                api = FakeAPI([before, before, before, after, after] if expected else [before]*3)
                clock = FakeClock()
                manager = CompactJobs(api, root=folder, clock=lambda: clock.now, sleep=clock.sleep)
                # Freeze policy time so the exact 30-minute boundary remains exact.
                from unittest.mock import patch
                with patch('compact_state.time.time', return_value=before['latestTurn']['completedAt']+age):
                    manager.prepare(THREAD_ID, 'Exact title', 'original', 'operation-auto')
                    manager.confirm('operation-auto', 'auto')
                    self.assertTrue(wait_for(lambda: manager.status('operation-auto')['state'].startswith('delivery_')))
                manager.close()
                self.assertEqual(sum(a == 'compact_start' for a, _ in api.calls), int(expected))
                self.assertEqual([v['message'] for a, v in api.calls if a == 'send'], ['original'])

    def test_three_strict_conditions_and_unknown_usage(self):
        from compact_state import should_compact
        data = snapshot(completed_at=1000)
        data['contextUsage'] = {'contextTokens': 80001}
        self.assertTrue(should_compact(data, now=2801))
        self.assertFalse(should_compact(data, now=2800))
        data['contextUsage']['contextTokens'] = 80000
        self.assertFalse(should_compact(data, now=2801))
        for value in (None, True, -1, '90000', float('nan')):
            data['contextUsage']['contextTokens'] = value
            self.assertFalse(should_compact(data, now=2801))
        data['contextUsage']['contextTokens'] = 90000
        data['compactState']['lastTurnIsCompaction'] = True
        self.assertFalse(should_compact(data, now=2801))

    def test_prepare_automatically_chooses_without_human_compact_confirmation(self):
        from compact_tools import dispatch
        bridge = Mock()
        bridge.compact_jobs.prepare.return_value = {'state': 'awaiting_confirmation', 'thread_id': THREAD_ID}
        args = dict(thread_id=THREAD_ID, expected_title='Exact title', message='original', operation_id='operation-auto')
        dispatch(bridge, 'prepare_send', args)
        bridge.compact_jobs.confirm.assert_called_once_with('operation-auto', 'auto')

    def test_usage_is_current_context_bound_to_latest_turn_not_lifetime_total(self):
        from context_usage import read_context_usage
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            sessions = home / 'sessions'
            sessions.mkdir()
            log = sessions / 'synthetic.jsonl'
            db = sqlite3.connect(home / 'state_5.sqlite')
            db.execute('CREATE TABLE threads (id TEXT, rollout_path TEXT)')
            db.execute('INSERT INTO threads VALUES (?,?)', (THREAD_ID, str(log)))
            db.commit()
            db.close()
            events = [dict(type='event_msg', payload=dict(type='task_started', turn_id='turn0')),
                      dict(type='event_msg', payload=dict(type='token_count', info=dict(
                          last_token_usage=dict(total_tokens=81000), total_token_usage=dict(total_tokens=9000000))))]
            log.write_text('\n'.join(map(json.dumps, events)), encoding='utf-8')
            self.assertEqual(read_context_usage(THREAD_ID, 'turn0', home)['contextTokens'], 81000)
            self.assertIsNone(read_context_usage(THREAD_ID, 'other', home)['contextTokens'])
            events.append(dict(type='event_msg', payload=dict(type='task_started', turn_id='new')))
            log.write_text('\n'.join(map(json.dumps, events)), encoding='utf-8')
            self.assertIsNone(read_context_usage(THREAD_ID, 'turn0', home)['contextTokens'])


if __name__ == '__main__':
    unittest.main()
