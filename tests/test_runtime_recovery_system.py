"""Protocol contracts: canonical bytes, blind boundaries, and audited model actions."""
import json
import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from agenttx.harness import CodingAgentHarness
from experiments.scripts.bench_runtime_recovery_pilot import append_history
from experiments.scripts.bench_runtime_recovery_system import (
    POLICIES, apply_coarse_policy, audit_commands, expected_artifacts,
    materialize, render_workload, snapshot, workload_spec,
)
from experiments.workloads.intra_turn_recovery_dag import build_intra_turn_plan


def plan():
    return build_intra_turn_plan(16, fault_position=.25, closure_ratio=.25,
                                shape="layered", interleaving="alternating", seed=0)


def test_canonical_artifacts_match_original_shell_operations(tmp_path):
    p = plan()
    spec = workload_spec(p)
    (tmp_path / 'workload.py').write_text(render_workload(spec))
    for step, expected in zip(p.steps, expected_artifacts(spec)):
        subprocess.run(step.command(p.steps), cwd=tmp_path, check=True)
        assert (tmp_path / step.relative_path).read_text() == expected
    root = p.fault_roots[0]
    subprocess.run([sys.executable, 'workload.py', 'run', str(root)], cwd=tmp_path,
                   env={**os.environ, 'AGENTTX_INJECT_PAYLOAD': 'stale\n'}, check=True)
    for step in p.steps[root+1:]:
        subprocess.run([sys.executable, 'workload.py', 'run', str(step.index)], cwd=tmp_path, check=True)
    invalid = {i for i, (s, want) in enumerate(zip(spec, expected_artifacts(spec)))
               if (tmp_path / s['path']).read_text() != want}
    assert invalid == set(p.oracle_closure)


@pytest.mark.parametrize('policy', ['turn_checkpoint', 'temporal_checkpoint', 'whole_branch_abort'])
def test_coarse_boundary_has_no_access_to_injector_truth(policy):
    class PublicPlan:
        steps = [SimpleNamespace(index=i) for i in range(6)]
        turn_ids = [0, 1, 2]
        @property
        def fault_roots(self):
            raise AssertionError('truth leak')
        oracle_closure = fault_roots
    probes = []
    class TX:
        ledger = SimpleNamespace(active_uncommitted=lambda: [SimpleNamespace(step_id=0)])
        conversation = SimpleNamespace(turns=[SimpleNamespace(kind='effect', turn_id=i+10) for i in range(3)])
        def run_verifier(self, argv, **kwargs):
            probes.append(argv[-2:])
            bad = int(argv[-1]) in ({1, 2} if argv[-2]=='check-turn' else {3, 5})
            return SimpleNamespace(step_id=len(probes)+6, returncode=int(bad))
        def rollback(self, i): return ('suffix', i)
        def rollback_turns(self, ids): return ('turns', ids)
    targets, records = apply_coarse_policy(TX(), PublicPlan(), policy)
    assert targets == {'turn_checkpoint': ('turns', [11, 12]),
                       'temporal_checkpoint': ('suffix', 3),
                       'whole_branch_abort': ('suffix', 0)}[policy]
    assert len(records) == len(probes)


def test_audit_rejects_batched_bypass_retained_rewrites_and_source_inspection():
    def events(command):
        return [dict(type='item.completed', item=dict(type='command_execution', id='a', command=command))]
    for command in ['cat workload.py', 'python workload.py run 3 && python workload.py run 4',
                    'python workload.py run 1', 'python -c "print(1)"']:
        assert not audit_commands(events(command), {1})['passed']
    assert audit_commands(events("/bin/bash -lc 'python workload.py run 3'"), {1})['passed']


@pytest.mark.parametrize('policy', POLICIES)
def test_real_runtime_canonical_policy_state(tmp_path, policy):
    p = plan(); spec = workload_spec(p)
    workspace = tmp_path/'ws'; workspace.mkdir()
    (workspace/'workload.py').write_text(render_workload(spec))
    h = CodingAgentHarness(workspace, session_dir=tmp_path/'session', trace_backend='strace')
    try:
        tx = h.tx
        tx.conversation.seed('Original task', 'Complete the artifact DAG')
        records = materialize(tx, p, policy)
        append_history(tx, p, records)
        before = snapshot(tx, spec)
        invalid = {i for i, want in enumerate(expected_artifacts(spec)) if before.get(i) != want}
        truth = set() if policy == 'clean_recovery' else set(p.oracle_closure)
        assert invalid == truth
        assert 'prior-version-value' not in json.dumps(tx.conversation.active_messages())
        if policy in {'turn_checkpoint', 'temporal_checkpoint', 'whole_branch_abort'}:
            # Even deliberately false captured context edges cannot expand a
            # turn checkpoint into a causal policy.
            targets, _ = apply_coarse_policy(tx, p, policy)
        elif policy == 'ideal_oracle': targets = tx.rollback_exact(sorted(truth))
        elif policy == 'causal':
            failed = tx.run_verifier([sys.executable,'workload.py','check-present'],trace_reads=True)
            recovery = tx.plan_recovery(failed,max_candidates=None,max_probes=24,allow_fallback=False,
                                        verifier_trace_reads=True)
            assert recovery.verified
            targets = tx.rollback_causal_many(recovery.root_step_ids)
        else: targets = []
        removed = set(targets).intersection(range(len(spec)))
        if policy not in {'clean_recovery','crash_no_rollback'}: assert truth <= removed
        survivors = set(range(len(spec))) - removed
        assert set(tx.conversation.active_step_ids()).intersection(range(len(spec))) == survivors
        after = snapshot(tx,spec)
        assert all(after.get(i)==before[i] for i in survivors)
        assert all(i not in after for i in removed)
        assert 'AGENTTX_INJECT_PAYLOAD' not in os.environ
    finally:
        h.close(destroy=True)


def test_multiturn_checkpoint_keeps_other_turns_even_with_false_context_edge(tmp_path):
    from experiments.workloads.intra_turn_recovery_dag import build_multiturn_controlled_waste_plan
    p=build_multiturn_controlled_waste_plan(4,turn_width=16,seed=3)
    spec=workload_spec(p)
    workspace=tmp_path/'ws';workspace.mkdir()
    (workspace/'workload.py').write_text(render_workload(spec))
    h=CodingAgentHarness(workspace,session_dir=tmp_path/'session',trace_backend='strace')
    try:
        tx=h.tx;tx.conversation.seed('Original task','Complete the DAG')
        records=materialize(tx,p,'turn_checkpoint');append_history(tx,p,records)
        failed_turns={s.turn_index for s in p.steps if s.index in p.oracle_closure}
        expected_removed={s.index for s in p.steps if s.turn_index in failed_turns}
        surviving=set(range(len(p.steps)))-expected_removed
        assert surviving and expected_removed
        for turn in tx.conversation.turns:
            for span in turn.calls:
                if span.step_id in surviving: span.parents=tuple(p.fault_roots)
        before=snapshot(tx,spec)
        targets,_=apply_coarse_policy(tx,p,'turn_checkpoint')
        assert set(targets)==expected_removed
        assert set(tx.conversation.active_step_ids())==surviving
        after=snapshot(tx,spec)
        assert all(after[i]==before[i] for i in surviving)
    finally:h.close(destroy=True)
