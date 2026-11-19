import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from tb_recovered_connection_20261027 import NOTICE, recovered_connection_evidence


class RecoveredConnectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.row = dict(state='completed', harness='codex', result_path=str(self.root/'host-result.json'))
        self.result = dict(completed=True, model_finished=True, model_returncode=0,
                           container_returncode=0, api_usage=dict(complete=True, total_tokens=123),
                           official_success=False, success=False)
        self.accounting = dict(model_summaries={'model-result.json': dict(
            returncode=0, original_sha256='retained-source-hash',
            usage_and_terminal_events=[dict(type='error', message=NOTICE),
                                       dict(type='turn.completed', usage=dict(input_tokens=100, output_tokens=23))]),
            'replay-result.json': None})

    def probe(self):
        (self.root/'host-result.json').write_text(json.dumps(self.result))
        self.accounting['host_result_sha256'] = hashlib.sha256((self.root/'host-result.json').read_bytes()).hexdigest()
        (self.root/'accounting-summary.json').write_text(json.dumps(self.accounting))
        return recovered_connection_evidence(self.row)

    def test_completed_model_with_failed_task_is_not_a_provider_failure(self):
        proof = self.probe()
        self.assertEqual(proof['recovered_notices'], 1)
        self.assertFalse(proof['official_success'])
        self.assertFalse(proof['additional_retry_authorized'])
        self.assertEqual(json.loads((self.root/'host-result.json').read_text()), self.result)

    def test_failed_or_incomplete_model_still_requires_review(self):
        for changes in [dict(model_returncode=1), dict(container_returncode=1),
                        dict(model_finished=False), dict(completed=False),
                        dict(api_usage=dict(complete=False))]:
            with self.subTest(changes=changes):
                original = copy.deepcopy(self.result)
                self.result.update(changes)
                self.assertIsNone(self.probe())
                self.result = original

    def test_terminal_failure_after_reconnect_is_not_recovered(self):
        events = self.accounting['model_summaries']['model-result.json']['usage_and_terminal_events']
        events.append(dict(type='turn.failed', error=dict(message='Connection refused')))
        self.assertIsNone(self.probe())

    def test_stream_reconnect_requires_completed_accounted_terminal(self):
        events = self.accounting['model_summaries']['model-result.json']['usage_and_terminal_events']
        events[0]['message'] = 'Reconnecting... 1/5 (stream disconnected before completion: stream closed before response.completed)'
        self.assertEqual(self.probe()['recovered_notices'], 1)
        self.result['api_usage']['complete'] = False
        self.assertIsNone(self.probe())
        self.result['api_usage']['complete'] = True
        events.insert(1, dict(type='turn.failed'))
        self.assertIsNone(self.probe())

    def test_unknown_error_is_not_suppressed_even_with_completed_turn(self):
        events = self.accounting['model_summaries']['model-result.json']['usage_and_terminal_events']
        events.insert(1, dict(type='error', message='Connection refused'))
        self.assertIsNone(self.probe())

    def test_prior_terminal_failure_is_not_suppressed(self):
        events = self.accounting['model_summaries']['model-result.json']['usage_and_terminal_events']
        events.insert(0, dict(type='turn.failed'))
        self.assertIsNone(self.probe())

    def test_failed_replay_is_not_suppressed(self):
        self.accounting['model_summaries']['replay-result.json'] = dict(returncode=1)
        self.assertIsNone(self.probe())

    def test_missing_or_damaged_evidence_is_not_suppressed(self):
        self.assertIsNone(recovered_connection_evidence(self.row))
        self.probe()
        (self.root/'accounting-summary.json').write_text('{')
        self.assertIsNone(recovered_connection_evidence(self.row))

    def test_unknown_terminal_or_no_reconnect_is_not_suppressed(self):
        model = self.accounting['model_summaries']['model-result.json']
        original = copy.deepcopy(model['usage_and_terminal_events'])
        for events in [[], original[:-1], [original[-1]], [None]]:
            with self.subTest(events=events):
                model['usage_and_terminal_events'] = events
                self.assertIsNone(self.probe())

    def test_other_harness_or_nonterminal_row_is_not_suppressed(self):
        for changes in [dict(harness='sweagent'), dict(state='running'), dict(state='infrastructure_error')]:
            with self.subTest(changes=changes):
                original = copy.deepcopy(self.row)
                self.row.update(changes)
                self.assertIsNone(self.probe())
                self.row = original

    def test_accounting_for_another_result_is_not_suppressed(self):
        self.probe()
        self.accounting['host_result_sha256'] = 'wrong-result'
        (self.root/'accounting-summary.json').write_text(json.dumps(self.accounting))
        self.assertIsNone(recovered_connection_evidence(self.row))

    def test_invalid_evidence_shapes_are_not_suppressed(self):
        original = copy.deepcopy(self.accounting)
        for summaries in [None, [], 'invalid']:
            with self.subTest(summaries=summaries):
                self.accounting = dict(original, model_summaries=summaries)
                self.assertIsNone(self.probe())
        self.accounting = original
        self.result['api_usage'] = 'invalid'
        self.assertIsNone(self.probe())


if __name__ == '__main__':
    unittest.main()
