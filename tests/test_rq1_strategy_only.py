"""Protocol checks before dispatching the model-free RQ1 campaign."""
import ast
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from experiments.scripts import run_rq1_strategy_only_20261110 as rq1


class StrategyOnlyProtocolTests(unittest.TestCase):
    def test_worker_override_resumes_only_pending_cells(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reg = dict(network_mode='none', model_calls=0, workers=8,
                       frozen_source=str(root), expected_cells=25)
            rq1.save(root / 'registration.json', reg)
            rq1.save(root / 'source-manifest.json', dict(files=[]))
            finished = dict(key='already-complete', state='passed', recovery_complete=True)
            pending = [dict(key=f'pending-{i}', phase='main', state='pending') for i in range(24)]
            rq1.save(root / 'cells.json', dict(runtime_gate=[], main=[finished, *pending]))
            rq1.save(root / 'progress.json', dict(started_at='2026-11-10T06:38:27+00:00'))
            with patch.object(rq1.subprocess, 'check_output', return_value=str(root)), \
                 patch.object(rq1, 'execute', return_value=dict(state='passed', recovery_complete=True)) as execute, \
                 patch.object(rq1, 'ThreadPoolExecutor', wraps=rq1.ThreadPoolExecutor) as pool, \
                 redirect_stdout(StringIO()):
                rq1.run(SimpleNamespace(cohort=root, workers=12))
            self.assertTrue(all(call.kwargs['max_workers'] == 12 for call in pool.call_args_list))
            self.assertEqual(execute.call_count, 24)
            progress = rq1.read(root / 'progress.json')
            self.assertEqual(progress['workers'], 12)
            self.assertEqual(progress['completed'], 25)
            self.assertEqual(progress['started_at'], '2026-11-10T06:38:27+00:00')
            self.assertEqual(rq1.read(root / 'registration.json'), reg)

    def test_canonical_matrix_and_recovery_invariants(self):
        cases = rq1.cases()
        self.assertEqual(len(cases), 400)
        self.assertEqual(len({c.plan.trace_id for c in cases}), 400)
        # Intersecting parameter sweeps include two identical DAGs under
        # different case IDs; preserve the registered 400-case design.
        self.assertEqual(len({c.plan.manifest_hash for c in cases}), 398)
        for case in cases:
            plan = case.plan
            affected = set(plan.oracle_closure)
            all_steps = {s.index for s in plan.steps}
            self.assertTrue(affected)
            self.assertTrue(set(plan.fault_roots) <= affected)
            for step in plan.steps:
                self.assertTrue(all(p < step.index for p in step.parent_indices))
                if affected.intersection(step.parent_indices):
                    self.assertIn(step.index, affected)
            self.assertEqual(rq1.expected_targets(plan, 'causal'), affected)
            self.assertEqual(rq1.expected_targets(plan, 'crash_no_rollback'), set())
            for policy in rq1.POLICIES:
                selected = rq1.expected_targets(plan, policy)
                self.assertTrue(selected <= all_steps)
                if policy != 'crash_no_rollback':
                    self.assertTrue(affected <= selected)

    def test_no_rollback_is_not_mistaken_for_complete_recovery(self):
        plan = rq1.cases()[0].plan
        row = dict(completed=True, rollback_ids=[], invalid_retained=list(plan.oracle_closure),
                   useful_discarded=[], conversation_matches_rollback=True,
                   retained_bytes_unchanged=True, missing_direct_edges=[],
                   runtime_only=True, recovery_safe=False)
        self.assertTrue(all(rq1.audit_policy(row, plan, 'crash_no_rollback').values()))
        self.assertFalse(row['recovery_safe'])
        row['rollback_ids'] = [plan.fault_roots[0]]
        self.assertFalse(all(rq1.audit_policy(row, plan, 'crash_no_rollback').values()))

    def test_selected_effects_follows_scope_without_claiming_completeness(self):
        complete = 0
        for case in rq1.cases():
            plan = case.plan
            selected = rq1.expected_targets(plan, 'root_only')
            affected = set(plan.oracle_closure)
            self.assertEqual(selected, set(plan.fault_roots))
            row = dict(completed=True, rollback_ids=sorted(selected),
                       invalid_retained=sorted(affected - selected), useful_discarded=[],
                       conversation_matches_rollback=True, retained_bytes_unchanged=True,
                       missing_direct_edges=[], runtime_only=True,
                       recovery_safe=affected <= selected)
            self.assertTrue(all(rq1.audit_policy(row, plan, 'root_only').values()))
            complete += row['recovery_safe']
        self.assertEqual(complete, 10)

    def test_container_has_no_network_or_model_configuration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            reg = dict(cohort=str(root / 'results'), frozen_source=str(root / 'source'),
                       python_mounts=['/python-runtime'], python='/python-runtime/bin/python',
                       image_id='sha256:test', uid=1000, gid=1000)
            cell = dict(phase='main', key='case/causal/repeat-00', trace_id='case', policy='causal')
            cmd = rq1.container_command(reg, cell)
            self.assertEqual(cmd[cmd.index('--network') + 1], 'none')
            envs = [cmd[i + 1] for i, value in enumerate(cmd) if value == '--env']
            self.assertFalse(any(any(s in e for s in ('API_KEY', 'AUTH_FILE', 'CONFIG_FILE')) for e in envs))
            mounts = [cmd[i + 1] for i, value in enumerate(cmd) if value == '--mount']
            self.assertEqual(len(mounts), 3)
            self.assertFalse(any(any(s in m.lower() for s in ('auth', 'profile', 'codex', 'sweagent')) for m in mounts))
            self.assertNotIn('--model', cmd)

    def test_reused_runtime_keeps_all_model_execution_behind_runtime_only_guard(self):
        reg = rq1.read(rq1.REFERENCE / 'registration.json')
        tree = ast.parse((Path(reg['frozen_source']) / 'experiments/scripts/bench_runtime_recovery_system.py').read_text())
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run_cell')
        calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and n.func.attr == 'run_in_transaction']
        self.assertEqual(len(calls), 2)
        guarded = [n for n in ast.walk(fn) if isinstance(n, ast.If)
                   and ast.unparse(n.test) == 'not args.runtime_only']
        self.assertEqual(len(guarded), 1)
        nodes = set(ast.walk(guarded[0]))
        self.assertTrue(all(c in nodes for c in calls))


if __name__ == '__main__':
    unittest.main()
