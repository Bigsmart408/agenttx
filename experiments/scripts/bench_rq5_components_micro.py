"""Native-agent component ablation on unchanged canonical micro workloads."""
from __future__ import annotations
import copy
import dataclasses
import json
import os
from pathlib import Path
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]


def run(trace_id, harness, variant, output, *, runtime_only=False):
    from agenttx.conversation import ConversationLog
    from agenttx.harness import CodingAgentHarness
    from experiments.scripts import bench_runtime_recovery_system as runtime
    from experiments.scripts.bench_external_recovery_artifacts import motivation_400_cases
    from experiments.scripts.recovery_cross_ablation import history_prompt
    plan = next(c.plan for c in motivation_400_cases() if c.plan.trace_id == trace_id)
    restore = variant in {'filesystem_only', 'agenttx'}
    synchronize = variant in {'context_only', 'agenttx'}
    output.mkdir(parents=True, exist_ok=True)
    work = output/'workspace'
    work.mkdir()
    spec = runtime.workload_spec(plan)
    tool = runtime.render_workload(spec)
    (work/'workload.py').write_text(tool)
    task = (f'Complete all {len(spec)} indexed operations of the original DAG in this directory. '
            f'Use {sys.executable} workload.py run INDEX in topological index order for '
            'operations that need repair, preserving valid retained outputs. Each operation '
            'reads its parent artifacts. Check completion with workload.py check. '
            'Use only these public workload commands (check-step INDEX and check-turn TURN are also allowed). '
            'Run exactly one command per tool call. Do not read/edit workload.py or inspect/write artifacts directly. '
            'Do not use loops, chained commands, or batch operations. Keep workload.py unchanged. '
            'Stop when the original workload passes; SWE-agent should then submit.')
    row = dict(protocol='agenttx.rq5-file-components-micro/v1', benchmark='micro',
               trace_id=trace_id, manifest_hash=plan.manifest_hash, harness=harness,
               cross_variant=variant, model='gpt-5.6-luna', reasoning_effort='max',
               context_profile='full', runtime_only=runtime_only, timings={})
    started = time.perf_counter()
    h = None
    try:
        h = CodingAgentHarness(work, session_dir=output/'session', trace_backend='strace',
                               ephemeral_roots=(Path('/root'),))
        tx = h.tx
        tx.conversation.seed('Complete the original artifact workload using shell tools.', task)
        records = runtime.materialize(tx, plan, 'causal')
        runtime.append_history(tx, plan, records)
        precrash = copy.deepcopy(tx.conversation.to_dict())
        before = runtime.snapshot(tx, spec)
        expected = runtime.expected_artifacts(spec)
        truth = set(plan.oracle_closure)
        missing_edges = [(s.index, p) for s in plan.steps for p in s.parent_indices
                         if p not in records[s.index].parents]
        if missing_edges or {i for i, want in enumerate(expected) if before.get(i) != want} != truth:
            raise RuntimeError('canonical injection or dependency capture mismatch')
        files = [plan.steps[i].relative_path for i in plan.fault_roots]
        checkpoint_count = len(list(tx.pool.layers.root.glob('before_*')))
        row['timings']['materialize_wall_s'] = time.perf_counter()-started
        stamp = time.perf_counter()
        outcome = tx.recover_file(files, checkpoint_step=-1, dry_run=not restore)
        if not outcome.audit['valid'] or set(outcome.plan.rollback_targets) != truth:
            raise RuntimeError('file-report plan differs from independent workflow truth')
        targets = list(outcome.plan.rollback_targets)
        if not restore:
            tx.conversation.rewind(targets, 'Continue the original task from the current file state.',
                                   expand_dependencies=False)
        if not synchronize:
            tx.conversation = ConversationLog.from_dict(precrash)
        tx._persist()
        row['timings']['policy_wall_s'] = time.perf_counter()-stamp
        after = runtime.snapshot(tx, spec)
        visible = set(tx.conversation.active_step_ids())
        active = {s.step_id for s in tx.ledger.steps if s.status != 'rolled_back'}
        all_ids = set(range(len(spec)))
        expected_active = all_ids-truth if restore else all_ids
        expected_visible = all_ids-truth if synchronize else all_ids
        physical = all(after.get(i) == (None if restore and i in truth else before.get(i)) for i in all_ids)
        proof = dict(protocol='agenttx.file-report-component-ablation/v1', variant=variant,
            restore_files=restore, synchronize_context=synchronize,
            policy_input=dict(kind='file', files=files, checkpoint_step=-1),
            checkpoint_configuration=dict(capture='before every tool call',
                history_end=len(spec), recorded_point_count=checkpoint_count, reference_checkpoint_step=-1),
            selected_causal_ids=targets, rollback_ids=targets if restore else [],
            active_original_step_ids=sorted(active), visible_original_step_ids=sorted(visible),
            original_observation_treatment_verified=visible == expected_visible,
            filesystem_treatment_verified=physical and active == expected_active,
            manifest_hash=plan.manifest_hash, candidate_probe_count=0)
        if not proof['original_observation_treatment_verified'] or not proof['filesystem_treatment_verified']:
            raise RuntimeError('component treatment does not match its registered switches')
        row['cross_treatment'] = proof
        for filename, value in [('cross-treatment.json', proof),
                                ('precrash-conversation.json', precrash),
                                ('cross-model-conversation.json', tx.conversation.to_dict())]:
            (output/filename).write_text(json.dumps(value, indent=2)+'\n')
        tx.dump_ledger(output/'recovered-ledger.json')
        if runtime_only:
            row.update(completed=True, success=True, total_tokens=0, model_calls=0,
                       api_usage=dict(complete=True, records=0, total_tokens=0))
        else:
            from experiments.scripts.run_system_microbenchmark import PROFILE, SWE
            if harness == 'sweagent':
                from experiments.scripts.sweagent_native_workload import SWEAgentNativeWorkloadHarness, load_codex_gpt_profile
                profile = load_codex_gpt_profile(PROFILE/'sweagent.toml', PROFILE/'auth.json',
                    model_override='gpt-5.6-luna', reasoning_effort_override='max')
                adapter = SWEAgentNativeWorkloadHarness(output_dir=output, profile=profile,
                    config_path=SWE/'config/bash_only.yaml', swe_agent_root=SWE,
                    max_steps=None, max_model_calls=0, timeout_s=3600)
                from experiments.scripts.micro_model_usage import model_usage
                meter = model_usage(output/'api-usage.json', 8_000_000)
            else:
                from agenttx.agents.external import CodexHarness
                from experiments.scripts.codex_micro_usage import metered_codex
                adapter = CodexHarness(model='gpt-5.6-luna', reasoning_effort='max', timeout_s=3600)
                meter = metered_codex(adapter, output/'api-usage.json', 8_000_000)
            stamp = time.perf_counter()
            handoff_task = (task + '\nUser report: the following file(s) are incorrect: '
                + json.dumps(files) + '. Reference checkpoint: -1.\n'
                'An interruption occurred. Continue from the current workspace.')
            prompt = history_prompt(tx.conversation, handoff_task)
            (output/'prompt.txt').write_text(prompt)
            with meter as usage:
                if harness == 'sweagent':
                    result = adapter.run_in_transaction(h, handoff_task, native_context=dict(
                        original_task=task, precrash_conversation=precrash, context_variant='full'))
                else:
                    result = adapter.run_in_transaction(h, prompt)
            row['timings']['model_wall_s'] = time.perf_counter()-stamp
            (output/'model-result.json').write_text(json.dumps(dataclasses.asdict(result), indent=2))
            audit = runtime.audit_commands(result.events, all_ids-truth)
            (output/'command-audit.json').write_text(json.dumps(audit, indent=2))
            stamp = time.perf_counter()
            check = tx.run_verifier([sys.executable, 'workload.py', 'check'], trace_reads=True, timeout_s=30)
            final = runtime.snapshot(tx, spec)
            source = tx._overlay_entry(work/'workload.py')
            verified = check.returncode == 0 and all(final.get(i) == want for i, want in enumerate(expected)) and source is not None and source.read_text() == tool
            row['timings']['verification_wall_s'] = time.perf_counter()-stamp
            row.update(completed=True, success=result.returncode == 0 and verified and audit['passed'],
                       model_returncode=result.returncode, verifier_passed=verified,
                       command_audit_passed=audit['passed'], api_usage=usage,
                       total_tokens=usage['total_tokens'], model_calls=len(usage['requests']),
                       prompt_tokens=usage['prompt_tokens'], completion_tokens=usage['completion_tokens'],
                       tool_calls=result.tool_calls)
        row['status'] = 'passed' if row['success'] else 'task_or_model_failed'
    except Exception as exc:
        row.update(completed=False, success=False, status='error', error=f'{type(exc).__name__}: {exc}')
        (output/'error.txt').write_text(traceback.format_exc())
    finally:
        row['timings']['full_wall_s'] = time.perf_counter()-started
        if h is not None:
            h.close(destroy=False)
        from experiments.scripts.experiment_credentials import private_values, redact_credentials, purge_private_codex_auth
        row = redact_credentials(row, private_values())
        (output/'result.json').write_text(json.dumps(row, indent=2)+'\n')
        purge_private_codex_auth(output, private_values())
        for directory, dirs, names in os.walk(output, followlinks=False):
            for p in [Path(directory), *(Path(directory)/name for name in dirs+names)]:
                try:
                    os.chown(p, 1003, 1003, follow_symlinks=False)
                except FileNotFoundError:
                    pass
    return row
