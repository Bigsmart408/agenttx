"""Adopt unchanged jobs after validating non-root SWE submit tool access."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_swe_clean_deadline_20261028 as clean_repair
checkpoint_repair = clean_repair.checkpoint_repair

base = checkpoint_repair.base
previous = checkpoint_repair.previous
user_repair = checkpoint_repair.user_repair
ancestor_repair = checkpoint_repair.ancestor_repair
profile_repair = checkpoint_repair.profile_repair
NAME = 'tb-dual-native-full-20261028-r54-swe-task-user-submit'
AUDIT = base.D/'validation/tb-swe-task-user-submit-repair-20261028'
FILES = ('src/agenttx/agents/swe_agent.py', 'src/agenttx/agents/swe_task_user_tools.py',
         'experiments/scripts/sweagent_native_workload.py',
         'experiments/scripts/bench_tb_image_native.py',
         'experiments/scripts/run_tb_dual_local.py')


def failure_evidence(row):
    if row['state'] not in base.TERMINAL:
        return None
    affected = json.loads((AUDIT/'affected-attempts.json').read_text())
    proof = next((r for r in affected['attempts'] if r['result_path'] == row.get('result_path')), None)
    if proof is None:
        return None
    assert proof['result_sha256'] == base.sha(row['result_path'])
    assert proof['old_proxy_sha256'] == base.sha(proof['old_proxy_source'])
    assert proof['source_matches_reproduced_baseline'] and proof['original_task_user'] == 'alice'
    assert affected['outcome_filter_used'] is False and affected['applies_to_all_six_policies']
    validation = json.loads((AUDIT/'regression-result.json').read_text())
    assert validation['baseline_submit_not_found_observations'] >= 1
    return dict(proof, classification='root_only_swe_submit_inaccessible_to_original_task_user',
                affected_attempts_path=str(AUDIT/'affected-attempts.json'),
                affected_attempts_sha256=base.sha(AUDIT/'affected-attempts.json'),
                original_costs_retained=True, outcome_based_retry=False)


def register_repair(cells, retries):
    latest = {base.key(row): row for row in cells}
    for row in retries:
        if row['state'] in base.TERMINAL:
            latest[base.key(row)] = row
    added = []
    for key, old in latest.items():
        history = [r for r in retries if base.key(r) == key]
        if any(r['state'] == 'running' or r.get('swe_submit_repair_cohort') == NAME
               for r in history):
            continue
        proof = failure_evidence(old)
        if proof is None:
            continue
        pending = next((r for r in history if r['state'] == 'pending'), None)
        first = next(r for r in cells if base.key(r) == key)
        if pending is None:
            pending = dict(task=key[0], harness=key[1], policy=key[2], state='pending',
                           expected_context=first['expected_context'],
                           attempt=max([0]+[r.get('attempt', 1) for r in history])+1,
                           original_state=first['state'], original_result_path=first['result_path'],
                           original_result_sha256=base.sha(first['result_path']))
            retries.append(pending)
        pending.update(swe_submit_repair_of=old['result_path'],
                       swe_submit_repair_cohort=NAME,
                       swe_submit_failure_evidence=proof,
                       previous_attempt_path=old['result_path'],
                       previous_attempt_sha256=base.sha(old['result_path']),
                       retry_reason='one_evidenced_original_user_swe_submit_environment_repair')
        added.append(pending)
    return added


def review_halt(progress):
    path = clean_repair.AUDIT/'activation/dispatch-halt.json'
    halt = json.loads(path.read_text())
    row = next(r for r in progress['tasks']+progress['retry_tasks']
               if r.get('result_path') == halt['row']['result_path'])
    proof = failure_evidence(row)
    assert proof and proof['result_sha256'] == base.sha(halt['row']['result_path'])
    return dict(proof, reviewed_environment_repair=True,
                validation=str(AUDIT/'regression-result.json'),
                dispatch_halt_sha256=base.sha(path))


def main():
    proof = json.loads((AUDIT/'regression-result.json').read_text())
    assert proof['returncode'] == 0 and proof['no_model_calls']
    assert proof['baseline_submit_not_found_observations'] >= 1
    stage = Path(proof['stage'])
    for row in proof['sources']:
        assert base.sha(stage/row['path']) == row['sha256']
    native = json.loads(Path(proof['native_preflight']).read_text())
    assert native['returncode'] == 0 and len(native['rows']) == 12 and native['complete']
    assert native['no_model_calls'] and not native['official_tests_executed']
    assert proof['all_six_swe_submit_preflights_passed']
    base.NAME, base.ROOT = NAME, base.D/'results'/NAME
    base.PREVIOUS = base.D/'results/tb-dual-native-full-20261028-r53-swe-clean-deadline'
    base.OLD_UNIT = 'agenttx-tb-dual-full-20261028-r53-swe-clean-deadline.service'
    base.AUDIT = AUDIT/'activation'
    base.FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
    base.REPAIR_FILES = FILES
    base.REPAIR_SOURCE_OVERRIDES = {p: stage/p for p in FILES}
    base.REVIEW_INHERITED_HALT = review_halt
    base.REVIEW_DEAD_COORDINATOR = None
    original_register, original_annotate, original_save = base.register_shutdown_repairs, base.annotate, base.save
    original_analysis = base.analysis_rows

    def analysis_rows(rows):
        analyzed = original_analysis(rows)
        for row in analyzed:
            if row.get('swe_submit_environment_exclusion'):
                # Analysis copy only: original official outcomes and all
                # historical spend remain in their immutable raw records.
                row['state'] = 'infrastructure_error'
                row['issue'] = 'root_only_swe_submit_inaccessible_to_original_task_user'
        return analyzed

    def register(cells, retries):
        original_register(cells, retries)
        previous.cache_repair.register_cache_repairs(cells, retries)
        previous.timeout_repair.register_timeout_repairs(cells, retries)
        previous.reference_repair.register_repairs(cells, retries)
        previous.index_repair.register(cells, retries, NAME, base.key)
        user_repair.register_repair(cells, retries)
        ancestor_repair.register_repair(cells, retries)
        profile_repair.register_repair(cells, retries)
        checkpoint_repair.register_repair(cells, retries)
        clean_repair.register_repair(cells, retries)
        added = register_repair(cells, retries)
        if added:
            with (AUDIT/'registrations.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=base.now(), attempts=added))+'\n')

    def annotate(rows, registry):
        original_annotate(rows, registry)
        previous.index_repair.annotate(rows)
        for row in rows:
            proof = failure_evidence(row)
            if proof:
                row['swe_submit_environment_exclusion'] = proof

    def select(cells, retries, active):
        urgent = next((r for r in retries if r['state'] == 'pending'
                       and (r.get('swe_submit_repair_of')
                            or r.get('swe_checkpoint_ancestor_repair_of')
                            or r.get('verifier_index_repair_of'))), None)
        return (urgent, True) if urgent else previous.reference_repair.repair.select(cells, retries, active)

    def save(path, data):
        path = Path(path)
        if path == base.ROOT/'registration.json':
            data = dict(data, coordinator_script=str(Path(__file__)),
                coordinator_script_sha256=base.sha(__file__),
                runtime_repair='original-task-user-private-swe-submit/v1',
                swe_submit_validation=str(AUDIT/'regression-result.json'),
                original_frozen_worker_sources_unchanged=False,
                clean_outer_deadline_loop_deployed=True, original_timeout_extended=False,
                unrelated_local_clean_deadline_loop_not_deployed=False,
                native_task_containers_restarted=True, other_workers_restarted=False,
                repaired_sweagent_attempts=6,
                task_user_swe_tools_contract='original-task-user-private-swe-submit/v1', deployed_native_source_changed=True,
                host_profile_modified=False, provider_configuration_changed=False,
                outcome_based_retries_added=False, download_proxy_default_enabled=True)
        if path == base.ROOT/'progress.json':
            data['download_transport_default'] = dict(enabled=True, domains='GitHub/HuggingFace',
                other_domains='DIRECT', port_8080='DIRECT', adopted_workers_keep_original_environment=True)
        if path == base.ROOT/'retry-report.json':
            data['swe_submit_repair'] = dict(original_model_timeout_s=3600,
                validation=str(AUDIT/'regression-result.json'),
                final_unknown_costs_excluded_from_complete_pairs=True,
                affected_attempts=str(AUDIT/'affected-attempts.json'),
                invalid_historical_attempts_excluded_from_valid_pairs=True,
                fixed_contract_kept_separate=True,
                raw_official_outcomes_unchanged=True, all_costs_retained=True)
        original_save(path, data)

    base.register_shutdown_repairs, base.annotate = register, annotate
    base.analysis_rows = analysis_rows
    base.select, base.save, base.requires_proxy = select, save, lambda row: True
    base.main()


if __name__ == '__main__':
    main()
