import tempfile
import unittest
from unittest.mock import Mock, patch

from backend import Bridge
import discovery


class TaskReviewTests(unittest.TestCase):
    def test_host_discovery_keeps_review_arguments_in_one_flat_object(self):
        from task_review import SCHEMA
        self.assertNotIn('oneOf',SCHEMA)
        self.assertTrue({'action','result','reason','skill_ids','evidence','expected_version'} <= SCHEMA['properties'].keys())

    def setUp(self):
        self.native = Mock()
        self.bridge = Bridge(native=self.native)
        self.addCleanup(self.bridge.close)
        self.start = self.bridge.call('hermes_context', {'task':'review fixture'}, client_scope='qa', require_bootstrap=True)
        self.bid = self.start['bootstrap']['bootstrap_id']

    def call(self, args, bid=None, scope='qa'):
        return self.bridge.call('hermes_task_review', {**args,'bootstrap_id':bid or self.bid},
                                client_scope=scope, require_bootstrap=True)

    def test_all_three_outcomes_are_observable_without_automatic_skill_writes(self):
        self.assertEqual(self.start['task_review']['state'],'pending')
        self.assertTrue(self.start['bootstrap']['execution_ready'])
        for outcome in ('no_new_learning','covered_by_existing_skill','new_reusable_learning'):
            with self.subTest(outcome=outcome):
                old=self.call({'action':'read'})
                result=self.call({'action':'submit','result':outcome,'expected_version':old['version'],
                    'skill_ids':[] if outcome=='no_new_learning' else ['software-development/hermes-web-mcp-workflows'],
                    'reason':'測試判斷','evidence':['fixture assertion']})
                self.assertEqual(result['state'],'recorded')
                self.assertEqual(result['result'],outcome)
                self.assertEqual(self.call({'action':'read'})['version'],result['version'])
        self.native.assert_not_called()
        self.assertFalse(self.bridge.specs['hermes_task_review']['annotations']['read_only_hint'])

    def test_idempotent_submit_explicit_revision_and_scope_isolation(self):
        args={'action':'submit','result':'no_new_learning','reason':'沒有新經驗','evidence':[]}
        first=self.call(args)
        self.assertEqual(self.call(args)['version'],1)
        self.assertEqual(self.call({'action':'submit','result':'no_new_learning','reason':'changed'})['errorCode'],
                         'REVIEW_VERSION_CONFLICT')
        self.assertEqual(self.call({'action':'read'},scope='other')['errorCode'],'bootstrap_required')
        new=self.bridge.call('hermes_context', {'task':'new task','bootstrap_id':self.bid},
                             client_scope='qa',require_bootstrap=True)
        self.assertEqual(new['task_review']['state'],'pending')
        self.assertEqual(self.call({'action':'read'})['errorCode'],'bootstrap_stale')
        self.assertEqual(first['version'],1)

    def test_validation_evidence_and_redaction(self):
        for field, value in [('reason',' '),('skill_ids',[' ']),('evidence',['\t'])]:
            args={'action':'submit','result':'covered_by_existing_skill','reason':'checked',
                  'skill_ids':['fixture'],'evidence':['assertion'],field:value}
            self.assertEqual(self.call(args).get('errorCode'),'REVIEW_EMPTY_FIELD')
            self.assertEqual(self.call({'action':'read'})['state'],'pending')
        with self.assertRaises(ValueError):
            self.call({'action':'submit','result':'unsupported'})
        with self.assertRaises(ValueError):
            self.call({'action':'read','reason':'unexpected'})
        with self.assertRaises(ValueError):
            self.call({'action':'submit','result':'no_new_learning'})
        result=self.call({'action':'submit','result':'covered_by_existing_skill','reason':'existing'})
        self.assertEqual(result['errorCode'],'REVIEW_EVIDENCE_REQUIRED')
        result=self.call({'action':'submit','result':'new_reusable_learning','skill_ids':['fixture'],
                          'reason':'API_KEY=sk-'+'a'*40,'evidence':['validated fixture']})
        self.assertNotIn('sk-',str(self.call({'action':'read'})))
        self.assertEqual(result['version'],1)
        self.assertNotIn('sk-',str(self.bridge.clients['qa']['task_review']))

    def test_new_bridge_has_no_review_and_cannot_accept_old_bootstrap(self):
        fresh=Bridge(native=Mock())
        self.addCleanup(fresh.close)
        result=fresh.call('hermes_task_review',{'action':'read','bootstrap_id':self.bid},
                          client_scope='qa',require_bootstrap=True)
        self.assertEqual(result['errorCode'],'bootstrap_required')
        boot=fresh.call('hermes_context',{'task':'fresh start'},client_scope='qa',require_bootstrap=True)
        self.assertEqual(boot['task_review'],{'state':'pending','version':0,'result':None})


if __name__=='__main__':
    unittest.main()
