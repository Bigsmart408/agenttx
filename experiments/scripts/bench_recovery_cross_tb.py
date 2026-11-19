#!/usr/bin/env python3
"""TB entry point for the registered filesystem/context factorial."""
from __future__ import annotations
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]


def main():
    from experiments.scripts import bench_tb_image_native as native
    from experiments.scripts.recovery_cross_ablation import install, VARIANTS, handoff, history_prompt
    variant = os.environ['AGENTTX_CROSS_VARIANT']
    harness = sys.argv[sys.argv.index('--harness')+1]
    output = Path(sys.argv[sys.argv.index('--output-dir')+1])
    policy = VARIANTS[variant][0]
    native.CONTEXTS.update({p: 'full' for p in native.POLICIES})
    treatment = install(variant, output, harness=harness)
    native.codex_recovery_prompt = lambda conversation, policy, prompt: history_prompt(conversation, prompt)
    original_command = native.build_command

    def build_command(*args, **kwargs):
        command = original_command(*args, **kwargs)
        command[command.index(str(Path(native.__file__).resolve()))] = str(Path(__file__).resolve())
        command[command.index('--entrypoint'):command.index('--entrypoint')] = [
            '-e', 'AGENTTX_CROSS_VARIANT='+variant]
        if os.environ.get('AGENTTX_CROSS_FILE_REPORT') == '1':
            command[command.index('--entrypoint'):command.index('--entrypoint')] = [
                '-e', 'AGENTTX_CROSS_FILE_REPORT=1']
        return command

    native.build_command = build_command
    original_completion = native.preflight_completion_contract

    def completion(harness_object):
        # An earlier original-user environment probe may intentionally leave
        # dirty files under no rollback. Reset that disposable probe before
        # running the separate completion and factorial checks.
        active = harness_object.tx.ledger.active_uncommitted()
        if active:
            harness_object.tx.rollback(active[0].step_id)
        proof = original_completion(harness_object)
        # The contract probe creates its own directories. Remove those effects
        # before testing localization so they cannot become synthetic ancestors
        # of the injected notes or verifier.
        active = harness_object.tx.ledger.active_uncommitted()
        if active:
            harness_object.tx.rollback(active[0].step_id)
        from experiments.workloads import terminal_bench_suite as tb
        from experiments.workloads import recovery_inject as injection
        from experiments.scripts import bench_official_tasks as bench
        task = tb.TBTask(**json.loads(Path('/agenttx-task.json').read_text()))
        docs, fault_after, _ = bench._randomized_crash_docs(task, task.task_id, 0)
        agent = type('Probe', (), {'harness': harness_object})()
        injected = tb.inject_task_trajectory(agent, task, sys.executable, docs=docs, fault_after_docs=fault_after)
        if not injected['tests_failed']:
            raise RuntimeError('cross TB preflight did not inject a failure')
        if policy == 'causal':
            targets, _ = bench._auto_localize_policy(agent, injected)
        else:
            targets, _ = bench._apply_blind_policy(agent, injected, policy)
        tx = harness_object.tx
        original_user = os.environ.get('AGENTTX_TB_TASK_USER') or 'root'
        identity = tx.run_tool('cross-original-user-after-policy',
            ['bash', '-c', 'test -w . && id -un'], trace_reads=False, timeout_s=30)
        if identity.returncode or identity.stdout.strip() != original_user:
            raise RuntimeError('cross treatment lost original user workspace access')
        paths = [injected['faulty_path'], *injected['derived_paths']]
        manifest = injection.build_recovery_manifest(policy=policy, ledger=tx.ledger,
            injected=injected, docs=docs, document_contents=injection.read_recovery_documents(agent, docs),
            workdir=Path(harness_object.workdir), rollback_targets=targets,
            path_exists={p: tx.path_exists(Path(harness_object.workdir)/p) for p in paths})
        if not manifest['authoritative'] or not treatment.applied:
            raise RuntimeError('cross TB preflight handoff was not verified')
        proof.update(cross_treatment=treatment.proof, external_model_calls=0,
                     post_treatment_original_user_verified=True, original_user=original_user)
        return proof

    native.preflight_completion_contract = completion
    return native.main()


if __name__ == '__main__':
    raise SystemExit(main())
