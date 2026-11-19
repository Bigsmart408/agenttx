"""Adopt unchanged jobs after evidenced snapshot ancestor metadata repair."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_task_user_workspace_20261028 as user_repair
previous = user_repair.previous
from experiments.scripts.bench_tb_image_native import TASK_USER_WORKSPACE_CONTRACT

base = previous.base
NAME = 'tb-dual-native-full-20261028-r50-rollback-ancestor-metadata'
AUDIT = base.D/'validation/tb-rollback-ancestor-repair-20261028'
FILES = ('experiments/scripts/bench_tb_image_native.py',
         'src/agenttx/layers.py')


def failure_evidence(row):
    if row['state'] not in base.TERMINAL or row['task'] != 'privilege-escalation':
        return None
    path = Path(row['result_path'])
    result = json.loads(path.read_text())
    if result.get('task_user_workspace_contract') == TASK_USER_WORKSPACE_CONTRACT:
        return None
    model_path = path.parent/'model-result.json'
    if model_path.is_file():
        model = json.loads(model_path.read_text())
        diagnostic_path = model_path
        model_sha = base.sha(model_path)
    else:
        # Non-Clean retention keeps exact original hashes and bounded stderr.
        diagnostic_path = path.parent/'accounting-summary.json'
        if not diagnostic_path.is_file():
            return None
        accounting = json.loads(diagnostic_path.read_text())
        assert accounting['host_result_sha256'] == base.sha(path)
        model = accounting.get('model_summaries', {}).get('model-result.json') or {}
        model_sha = model.get('original_sha256')
    stderr = model.get('stderr') or model.get('stderr_excerpt') or ''
    if not (result.get('original_task_user') == 'alice'
            and model.get('returncode') == 1
            and 'mkdir: cannot create directory' in stderr
            and '/home/alice/.agenttx-codex-' in stderr
            and 'Permission denied' in stderr):
        return None
    return dict(classification='incremental_snapshot_recreated_ancestor_metadata',
                result_path=str(path), result_sha256=base.sha(path),
                original_model_result_sha256=model_sha,
                diagnostic_path=str(diagnostic_path), diagnostic_sha256=base.sha(diagnostic_path))


def register_repair(cells, retries):
    latest = {base.key(row): row for row in cells}
    for row in retries:
        if row['state'] in base.TERMINAL:
            latest[base.key(row)] = row
    added = []
    for key, old in latest.items():
        history = [r for r in retries if base.key(r) == key]
        if any(r['state'] == 'running' or r.get('rollback_ancestor_repair_cohort') == NAME
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
        pending.update(rollback_ancestor_repair_of=old['result_path'],
                       rollback_ancestor_repair_cohort=NAME,
                       rollback_ancestor_failure_evidence=proof,
                       previous_attempt_path=old['result_path'],
                       previous_attempt_sha256=base.sha(old['result_path']),
                       retry_reason='one_evidenced_snapshot_ancestor_metadata_repair')
        added.append(pending)
    return added


def review_halt(progress):
    path = base.D/'validation/tb-task-user-workspace-repair-20261028/activation/dispatch-halt.json'
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
    stage = Path(proof['stage'])
    for row in proof['sources']:
        assert base.sha(stage/row['path']) == row['sha256']
    native = json.loads(Path(proof['native_preflight']).read_text())
    assert native['returncode'] == 0 and len(native['rows']) == 12 and native['complete']
    assert native['no_model_calls'] and not native['official_tests_executed']
    base.NAME, base.ROOT = NAME, base.D/'results'/NAME
    base.PREVIOUS = base.D/'results/tb-dual-native-full-20261028-r49-task-user-workspace'
    base.OLD_UNIT = 'agenttx-tb-dual-full-20261028-r49-task-user-workspace.service'
    base.AUDIT = AUDIT/'activation'
    base.FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
    base.REPAIR_FILES = FILES
    base.REPAIR_SOURCE_OVERRIDES = {p: stage/p for p in FILES}
    base.REVIEW_INHERITED_HALT = review_halt
    original_register, original_annotate, original_save = base.register_shutdown_repairs, base.annotate, base.save

    def register(cells, retries):
        original_register(cells, retries)
        previous.cache_repair.register_cache_repairs(cells, retries)
        previous.timeout_repair.register_timeout_repairs(cells, retries)
        previous.reference_repair.register_repairs(cells, retries)
        previous.index_repair.register(cells, retries, NAME, base.key)
        user_repair.register_repair(cells, retries)
        added = register_repair(cells, retries)
        if added:
            with (AUDIT/'registrations.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=base.now(), attempts=added))+'\n')

    def annotate(rows, registry):
        original_annotate(rows, registry)
        previous.index_repair.annotate(rows)

    def select(cells, retries, active):
        urgent = next((r for r in retries if r['state'] == 'pending'
                       and (r.get('rollback_ancestor_repair_of') or r.get('verifier_index_repair_of'))), None)
        return (urgent, True) if urgent else previous.reference_repair.repair.select(cells, retries, active)

    def save(path, data):
        path = Path(path)
        if path == base.ROOT/'registration.json':
            data = dict(data, coordinator_script=str(Path(__file__)),
                coordinator_script_sha256=base.sha(__file__),
                runtime_repair=TASK_USER_WORKSPACE_CONTRACT,
                task_user_workspace_validation=str(AUDIT/'regression-result.json'),
                original_frozen_worker_sources_unchanged=False,
                unrelated_local_clean_deadline_loop_not_deployed=True,
                native_task_containers_restarted=False, other_workers_restarted=False,
                outcome_based_retries_added=False, download_proxy_default_enabled=True)
        if path == base.ROOT/'progress.json':
            data['download_transport_default'] = dict(enabled=True, domains='GitHub/HuggingFace',
                other_domains='DIRECT', port_8080='DIRECT', adopted_workers_keep_original_environment=True)
        if path == base.ROOT/'retry-report.json':
            data['task_user_workspace_repair'] = dict(contract=TASK_USER_WORKSPACE_CONTRACT,
                validation=str(AUDIT/'regression-result.json'), mixed_histories_excluded=True)
        original_save(path, data)

    base.register_shutdown_repairs, base.annotate = register, annotate
    base.select, base.save, base.requires_proxy = select, save, lambda row: True
    base.main()


if __name__ == '__main__':
    main()
