#!/usr/bin/env python3
"""Continuously audit the Turn checkpoint supplement against frozen RQ2 controls.

Read experiment evidence only. Dispatch/recovery belongs to the existing RQ2
supervisor. Comparisons are provisional and never select the best retry.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'src')]
from experiments.scripts.run_tb_dual_local import configuration_id, result_configuration
from scripts.maintenance.supervise_rq2_turn_checkpoint_20261111 import verify_sources

POLICIES = ('causal', 'temporal_checkpoint', 'whole_branch_abort',
            'crash_no_rollback', 'ideal_oracle', 'clean_recovery')
TB_AUDIT = Path('/mnt/data/agenttx-runtime/validation/tb-category-effects-20261029/20261030T051423Z')
SWE_AUDIT = Path('/mnt/data/agenttx-runtime/validation/swe-paper-comparison-20261019')
USAGE = re.compile(r'usage=Usage\(completion_tokens=(\d+),prompt_tokens=(\d+),total_tokens=(\d+)')


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    return json.loads(Path(path).read_text())


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')
    temporary.replace(path)


def project(raw, path, benchmark, harness, state, expected_hash=None):
    """Retain accounting/configuration fields; exclude logs and credentials."""
    accounting = {}
    if benchmark == 'tb':
        usage = raw.get('api_usage') or {}
        complete = usage.get('complete') is True
        tokens = usage.get('total_tokens')
        config = result_configuration(raw, path)
        duration = (raw.get('timings') or {}).get('full_wall_s')
        seed = raw.get('fault_trial_seed')
        policy = raw.get('policy')
    else:
        main, replay = raw.get('total_tokens'), raw.get('doc_replay_tokens')
        complete = (type(main) is int and main > 0 and type(replay) is int and replay >= 0
                    and raw.get('usage_source') in ('stdout_jsonl', 'sweagent_model_stats')
                    and raw.get('harness_returncode') == 0 and raw.get('finished') is True)
        if harness == 'codex':
            events = []
            for line in raw.get('harness_stdout', '').splitlines():
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                if isinstance(event, dict) and event.get('type') == 'turn.completed':
                    events.append(event)
            complete = complete and len(events) == 1
            if len(events) == 1:
                usage = events[0].get('usage') or {}
                complete = complete and usage.get('input_tokens', -1) + usage.get('output_tokens', -1) == main
        complete = complete and replay == (raw.get('doc_replay_prompt_tokens', -1)
                                           + raw.get('doc_replay_completion_tokens', -1))
        if raw.get('doc_replay_needed'):
            complete = complete and bool(replay and raw.get('documents_valid'))
        tokens = main + replay if type(main) is int and type(replay) is int else None
        if harness == 'sweagent':
            # SWE-agent's summary omits some returned API usage. Use exactly
            # the per-response accounting already used by the published audit.
            log_path = path.parent / 'worker.log'
            log_bytes = log_path.read_bytes()
            matches = USAGE.findall(re.sub(r'\s+', '', log_bytes.decode(errors='replace')))
            expected_calls = raw.get('model_calls', 0) + raw.get('doc_replay_tool_calls', 0)
            complete = bool(matches) and len(matches) == expected_calls
            tokens = sum(int(m[2]) for m in matches) if matches else None
            accounting = dict(usage_source='returned_api_usage_records', reported_tokens=main,
                              usage_records=len(matches), expected_calls=expected_calls,
                              usage_log=str(log_path), usage_log_sha256=hashlib.sha256(log_bytes).hexdigest())
        config = None
        duration = raw.get('wall_s')
        seed = raw.get('crash_trial_seed')
        policy = raw.get('mode')
    return dict(task=raw['task'], benchmark=benchmark, harness=harness, policy=policy,
                state=state, success=raw.get('success') is True, complete_usage=bool(complete),
                tokens=tokens, duration_s=duration, model=raw.get('model'),
                context=raw.get('recovery_context_variant'), trial_seed=seed,
                configuration=config, configuration_id=configuration_id(config) if config else None,
                result_path=str(path), result_sha256=expected_hash or digest(path), **accounting)


def history_summary(attempts):
    latest = attempts[-1]
    configurations = {a['configuration_id'] for a in attempts}
    complete = all(a['complete_usage'] and a['state'] == 'completed'
                   and type(a['tokens']) is int and a['tokens'] > 0 for a in attempts)
    times = [a['duration_s'] for a in attempts]
    return dict(latest, attempts=len(attempts),
                complete_history=complete, mixed_configuration=len(configurations) != 1,
                cumulative_tokens=sum(a['tokens'] for a in attempts) if complete else None,
                cumulative_duration_s=sum(times) if all(type(t) in (int, float) and t > 0 for t in times) else None)


def build_baseline(directory):
    """Pin audited controls once, with every available source result hash."""
    if (directory / 'baseline.json').exists():
        raise FileExistsError('baseline already pinned')
    analysis_path = ROOT / 'review/realworld-results-20261106/analysis.json'
    analysis = read(analysis_path)
    sources = {str(analysis_path): digest(analysis_path)}
    for source in analysis['sources']:
        assert digest(source['path']) == source['sha256'], 'audited input changed'
        sources[source['path']] = source['sha256']
    attempts = defaultdict(list)
    usage_report = read(SWE_AUDIT / 'report.json')
    # This live audit is periodically regenerated (including checked_at). Pin
    # its current bytes and reproduce every count from the immutable API logs;
    # the original phase-accounting input above remains hash-verified.
    sources[usage_report['source_audit']] = digest(usage_report['source_audit'])
    swe_hashes = {row['result']: row for row in read(usage_report['source_audit'])['rows']}
    for row in read(SWE_AUDIT / 'phase-accounting.json'):
        path = Path(row['result'])
        expected = swe_hashes[str(path)]
        assert digest(path) == expected['result_sha256'], 'SWE baseline result changed'
        item = project(read(path), path, 'swe', 'sweagent', 'completed')
        assert item['usage_log_sha256'] == expected['log_sha256'], 'SWE baseline usage log changed'
        assert item['tokens'] == row['api_main'] + row['api_replay']
        assert item['success'] == bool(row['success'])
        attempts['swe/sweagent/' + row['task'] + '/' + row['policy']].append(item)
    audit_path = ROOT / 'review/realworld-results-20261106/codex-swe-cell-audit.json'
    sources[str(audit_path)] = digest(audit_path)
    for row in read(audit_path):
        path = Path(row['result_path'])
        if not row['result_sha256']:
            continue
        assert digest(path) == row['result_sha256'], 'Codex baseline result changed'
        item = project(read(path), path, 'swe', 'codex', row['state'], row['result_sha256'])
        assert item['success'] == bool(row['success'])
        attempts['swe/codex/' + row['task'] + '/' + row['policy']].append(item)
    manifest_path = ROOT / 'review/realworld-results-20261101/source-attempts.json'
    sources[str(manifest_path)] = digest(manifest_path)
    hashes = read(manifest_path)['attempts']
    progress = read(TB_AUDIT / 'source-progress.json')
    for row in progress['tasks'] + progress['retry_tasks']:
        path = Path(row['result_path'])
        expected = hashes[str(path)]['sha256']
        assert digest(path) == expected, 'TB baseline result changed'
        # Failed container launches can have only host_error/status; identity
        # still comes from the audited dispatch record, never from log text.
        original = read(path)
        assert original.get('task', row['task']) == row['task']
        assert original.get('policy', row['policy']) == row['policy']
        raw = dict(original, task=row['task'], policy=row['policy'])
        item = project(raw, path, 'tb', row['harness'], row['state'], expected)
        attempts['tb/' + row['harness'] + '/' + row['task'] + '/' + row['policy']].append(item)
    result = dict(created_at=now(), sources=sources, attempts=dict(attempts),
                  cells={key: history_summary(rows) for key, rows in attempts.items()})
    # Reproduce audited policy-level outcomes before accepting the pinned index.
    for group, cohort in analysis['cohorts'].items():
        benchmark, harness = group.split('_', 1)
        for policy, outcome in cohort['outcomes'].items():
            selected = [v for v in result['cells'].values()
                        if (v['benchmark'], v['harness'], v['policy']) == (benchmark, harness, policy)]
            assert sum(v['success'] for v in selected) == outcome['success'], (group, policy)
    save(directory / 'baseline.json', result)
    save(directory / 'baseline-manifest.json', dict(sha256=digest(directory / 'baseline.json'),
          sources=sources, created_at=result['created_at'], cells=len(result['cells']),
          attempts=sum(map(len, attempts.values()))))


def exclusion(turn, baseline):
    if baseline is None:
        return 'baseline_missing'
    if not turn['success'] or not baseline['success']:
        return 'not_both_successful'
    if not turn['complete_history'] or not baseline['complete_history']:
        return 'incomplete_usage_or_infrastructure_history'
    if turn['mixed_configuration'] or baseline['mixed_configuration']:
        return 'mixed_environment_history'
    if turn['model'] != baseline['model']:
        return 'model_mismatch'
    if turn['configuration_id'] != baseline['configuration_id']:
        return 'environment_or_budget_mismatch'
    if baseline['policy'] != 'clean_recovery' and turn['trial_seed'] != baseline['trial_seed']:
        return 'fault_trial_mismatch'
    return None


def compare(turns, baseline):
    comparisons = {}
    for group in ('swe/sweagent', 'swe/codex', 'tb/sweagent', 'tb/codex'):
        selected = {k: v for k, v in turns.items() if k.startswith(group + '/')}
        output = dict(observed=len(selected), success=sum(v['success'] for v in selected.values()), policies={})
        for policy in POLICIES:
            pairs, excluded, mismatch_examples = [], Counter(), []
            matched, turn_success, baseline_success = 0, 0, 0
            for key, turn in sorted(selected.items()):
                control = baseline.get(key + '/' + policy)
                if control is not None:
                    matched += 1
                    turn_success += turn['success']
                    baseline_success += control['success']
                reason = exclusion(turn, control)
                if reason:
                    excluded[reason] += 1
                    if reason == 'environment_or_budget_mismatch' and len(mismatch_examples) < 3:
                        mismatch_examples.append(dict(task=turn['task'], turn=turn['configuration'],
                                                      baseline=control['configuration']))
                    continue
                pairs.append(dict(task=turn['task'], baseline_tokens=control['cumulative_tokens'],
                    turn_tokens=turn['cumulative_tokens'], baseline_s=control['cumulative_duration_s'],
                    turn_s=turn['cumulative_duration_s'], baseline_context=control['context'],
                    turn_context=turn['context'], baseline_attempts=control['attempts'],
                    turn_attempts=turn['attempts'], configuration_id=turn['configuration_id']))
            a, b = sum(p['baseline_tokens'] for p in pairs), sum(p['turn_tokens'] for p in pairs)
            timed = [p for p in pairs if p['baseline_s'] and p['turn_s']]
            ta, tb = sum(p['baseline_s'] for p in timed), sum(p['turn_s'] for p in timed)
            output['policies'][policy] = dict(matched_terminal_tasks=matched,
                turn_success_on_matched_tasks=turn_success, baseline_success_on_matched_tasks=baseline_success,
                eligible_cost_pairs=len(pairs), excluded=dict(excluded), baseline_tokens=a, turn_tokens=b,
                configuration_mismatch_examples=mismatch_examples,
                baseline_token_saving_vs_turn_pct=100 * (1 - a / b) if b else None,
                timed_pairs=len(timed), baseline_time_saving_vs_turn_pct=100 * (1 - ta / tb) if tb else None,
                pairs=pairs)
        comparisons[group] = output
    return comparisons


def audit(cohort, baseline, cache):
    registration = read(cohort / 'registration.json')
    cells = read(cohort / 'cells.json')
    assert len(cells) == registration['expected_cells'] == 1082
    assert len({c['key'] for c in cells}) == len(cells)
    expected = {f'swe/{h}/{r["task"]}' for h in ('sweagent', 'codex') for r in registration['swe_images']}
    expected |= {f'tb/{h}/{t}' for h in ('sweagent', 'codex') for t in registration['tb_tasks']}
    assert {c['key'] for c in cells} == expected, 'task set changed'
    for benchmark in ('swe', 'tb'):
        assert digest(registration[benchmark + '_reference']) == registration[benchmark + '_reference_sha256']
    turns, errors, terminal = {}, [], []
    for cell in cells:
        if cell['state'] not in ('completed', 'infrastructure_error'):
            continue  # Never compare the first failed attempt while its registered retry is pending.
        terminal.append(cell)
        attempts = []
        for attempt in cell['attempts']:
            path = Path(attempt['result_path'])
            expected_hash = attempt.get('result_sha256')
            if not expected_hash or not path.is_file():
                errors.append(dict(key=cell['key'], reason='missing_terminal_result'))
                continue
            actual = digest(path)
            assert actual == expected_hash, 'terminal result changed: ' + cell['key']
            if actual not in cache:
                raw = read(path)
                assert raw['task'] == cell['task']
                item = project(raw, path, cell['benchmark'], cell['harness'],
                    'infrastructure_error' if attempt.get('infrastructure_issue') else 'completed', actual)
                assert item['policy'] == 'turn_checkpoint' and item['context'] == 'full'
                assert item['model'] == registration['model']
                if cell['benchmark'] == 'swe':
                    assert raw['source_sha256'] == registration['source_sha256']
                    assert raw['harness_backend'] == cell['harness']
                else:
                    assert raw['harness'] == cell['harness']
                    assert raw['reasoning_effort'] == registration['reasoning_effort']
                    assert raw['fault_seed'] == registration['tb_fault_seed']
                cache[actual] = item
            if cache[actual].get('usage_log'):
                assert digest(cache[actual]['usage_log']) == cache[actual]['usage_log_sha256'], 'terminal usage log changed'
            attempts.append(cache[actual])
        if len(attempts) == len(cell['attempts']) and attempts:
            turns[cell['key']] = history_summary(attempts)
    counts = dict(Counter(c['state'] for c in cells))
    supervisor = read(cohort / 'supervisor/latest.json')
    progress = read(cohort / 'progress.json')
    age = time.time() - datetime.fromisoformat(progress['updated_at']).timestamp()
    alerts = list(errors)
    if progress.get('active') and age > 120:
        alerts.append(dict(reason='progress_stale', age_s=age))
    supervisor_age = time.time() - datetime.fromisoformat(supervisor['checked_at']).timestamp()
    if supervisor_age > 180 and counts.get('pending', 0):
        alerts.append(dict(reason='supervisor_stale', age_s=supervisor_age))
    if supervisor['state'] not in ('running', 'pending_resumed', 'coordinator_starting_or_draining',
                                    'finished', 'waiting_for_provider_probe', 'waiting_for_provider_recovery'):
        alerts.append(dict(reason='supervisor_attention', state=supervisor['state']))
    comparisons = compare(turns, baseline['cells'])
    for group, results in comparisons.items():
        causal = results['policies']['causal']
        mismatches = causal['excluded'].get('environment_or_budget_mismatch', 0)
        if mismatches:
            alerts.append(dict(reason='historical_control_configuration_mismatch', group=group,
                               affected_success_pairs=mismatches,
                               strict_eligible_pairs=causal['eligible_cost_pairs']))
    return dict(checked_at=now(), provisional=True, counts=counts,
                completed_task_success=sum(c['state'] == 'completed' and c.get('success') is True for c in terminal),
                infrastructure_reported_success=sum(c['state'] == 'infrastructure_error' and c.get('success') is True for c in terminal),
                terminal_task_failures=sum(c['state'] == 'completed' and not c.get('success') for c in terminal),
                infrastructure_issues=dict(Counter(c.get('infrastructure_issue') for c in terminal
                                                  if c['state'] == 'infrastructure_error')),
                supervisor_state=supervisor['state'], progress_age_s=age, alerts=alerts,
                comparisons=comparisons,
                limits=['Partial task set; do not treat progress estimates as final conclusions.',
                        'Context differs by policy: Turn checkpoint uses full, AgentTX uses compact.',
                        'TB histories include every recorded failed/retry attempt; unknown usage is excluded.',
                        'Compatible configuration and fault trial required; no cross-agent pooling.',
                        'These are controlled recovery experiments, formal_eligible=false.'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', type=Path, required=True)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    directory = args.cohort / 'comparison-monitor'
    directory.mkdir(exist_ok=True)
    if args.prepare:
        build_baseline(directory)
        return
    manifest = read(directory / 'baseline-manifest.json')
    assert digest(directory / 'baseline.json') == manifest['sha256'], 'pinned baseline changed'
    baseline, cache = read(directory / 'baseline.json'), {}
    registered_hash = digest(args.cohort / 'registration.json')
    last_source_check = 0
    while True:
        try:
            assert digest(directory / 'baseline.json') == manifest['sha256']
            assert digest(args.cohort / 'registration.json') == registered_hash
            if time.monotonic() - last_source_check > 300:
                verify_sources(args.cohort)
                last_source_check = time.monotonic()
            status = audit(args.cohort, baseline, cache)
            status['frozen_source_and_profiles_verified'] = True
        except Exception as exc:
            # Exception messages from external logs are deliberately not copied here.
            status = dict(checked_at=now(), provisional=True,
                          alerts=[dict(reason='comparison_audit_error', error_type=type(exc).__name__)])
        save(directory / 'latest.json', status)
        if status['alerts']:
            save(directory / 'attention.json', status)
        with (directory / 'history.jsonl').open('a') as stream:
            stream.write(json.dumps({k: v for k, v in status.items() if k != 'comparisons'}) + '\n')
        if args.once or (status.get('supervisor_state') == 'finished' and not status['alerts']):
            return
        time.sleep(60)


if __name__ == '__main__':
    main()
