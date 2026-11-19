"""Resume one old SWE Checkpoint attempt with validated ancestor metadata."""
import json
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_private_profile_20261028 as profile_repair
ancestor_repair = profile_repair.ancestor_repair
user_repair = ancestor_repair.user_repair
previous = ancestor_repair.previous
from experiments.scripts.bench_tb_image_native import TASK_USER_WORKSPACE_CONTRACT

base = previous.base
NAME = 'tb-dual-native-full-20261028-r52-swe-checkpoint-ancestor'
AUDIT = base.D/'validation/tb-swe-checkpoint-ancestor-repair-20261028'
FILES = ()


def review_dead_coordinator(pointer):
    assert pointer['cohort'] == 'tb-dual-native-full-20261028-r51-private-profile-access'
    assert base.prior.state(pointer['coordinator_pid']) in (None, 'Z')
    unit = 'agenttx-tb-dual-full-20261028-r51-private-profile-access.service'
    status = subprocess.check_output(['systemctl','--user','show',unit,
        '-p','MainPID','-p','ActiveState','-p','Result'], text=True)
    assert all(x in status.splitlines() for x in ('MainPID=0','ActiveState=failed','Result=exit-code'))
    journal = subprocess.check_output(['journalctl','--user','-u',unit,'-n','35','--no-pager'], text=True)
    assert 'AttributeError:' in journal and "'NoneType' object has no attribute 'get'" in journal
    assert 'run_tb_rollback_ancestors_20261028.py' in journal
    proof = dict(coordinator_dead=True, coordinator_pid=pointer['coordinator_pid'],
        unit=unit, status=status, root_cause='nullable retained model summary',
        parser_repair_sha256=base.sha(Path(__file__).with_name('run_tb_rollback_ancestors_20261028.py')),
        regression_tests='tests/test_tb_rollback_ancestor_evidence.py: 3 passed',
        existing_workers_restarted=False)
    (AUDIT/'dead-coordinator-journal.log').write_text(journal)
    return proof


def failure_evidence(row):
    if row['state'] not in base.TERMINAL:
        return None
    enforcement_path = AUDIT/'enforcement.json'
    enforcement = json.loads(enforcement_path.read_text())
    if row.get('result_path') != enforcement['result_path']:
        return None
    assert enforcement['worker_stopped']
    assert base.sha(row['result_path']) == enforcement['terminal_result_sha256']
    observation = Path(enforcement['observation_path'])
    assert base.sha(observation) == enforcement['observation_sha256']
    original = json.loads(observation.read_text())
    assert original['evidence']['upper_workspace'] == dict(uid=0,gid=0,mode='0o755',access_acl=None)
    assert (row['task'],row['harness'],row['policy']) == ('privilege-escalation','sweagent','temporal_checkpoint')
    return dict(classification='old_swe_checkpoint_recreated_ancestor_metadata',
                result_path=row['result_path'],result_sha256=base.sha(row['result_path']),
                observation_path=str(observation),observation_sha256=base.sha(observation),
                enforcement_path=str(enforcement_path),enforcement_sha256=base.sha(enforcement_path),
                stopped_for_environment_repair=True,final_usage_unknown_retained=True)


def register_repair(cells, retries):
    latest = {base.key(row): row for row in cells}
    for row in retries:
        if row['state'] in base.TERMINAL:
            latest[base.key(row)] = row
    added = []
    for key, old in latest.items():
        history = [r for r in retries if base.key(r) == key]
        if any(r['state'] == 'running' or r.get('swe_checkpoint_ancestor_repair_cohort') == NAME
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
        pending.update(swe_checkpoint_ancestor_repair_of=old['result_path'],
                       swe_checkpoint_ancestor_repair_cohort=NAME,
                       swe_checkpoint_ancestor_failure_evidence=proof,
                       previous_attempt_path=old['result_path'],
                       previous_attempt_sha256=base.sha(old['result_path']),
                       retry_reason='one_evidenced_old_swe_checkpoint_ancestor_metadata_repair')
        added.append(pending)
    return added


def review_halt(progress):
    path = base.D/'validation/tb-private-profile-repair-20261028/activation/dispatch-halt.json'
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
    base.PREVIOUS = base.D/'results/tb-dual-native-full-20261028-r51-private-profile-access'
    base.OLD_UNIT = 'agenttx-tb-dual-full-20261028-r51-private-profile-access.service'
    base.AUDIT = AUDIT/'activation'
    base.FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
    base.REPAIR_FILES = FILES
    base.REPAIR_SOURCE_OVERRIDES = {p: stage/p for p in FILES}
    base.REVIEW_INHERITED_HALT = review_halt
    base.REVIEW_DEAD_COORDINATOR = review_dead_coordinator
    original_register, original_annotate, original_save = base.register_shutdown_repairs, base.annotate, base.save

    def register(cells, retries):
        original_register(cells, retries)
        previous.cache_repair.register_cache_repairs(cells, retries)
        previous.timeout_repair.register_timeout_repairs(cells, retries)
        previous.reference_repair.register_repairs(cells, retries)
        previous.index_repair.register(cells, retries, NAME, base.key)
        user_repair.register_repair(cells, retries)
        ancestor_repair.register_repair(cells, retries)
        profile_repair.register_repair(cells, retries)
        added = register_repair(cells, retries)
        if added:
            with (AUDIT/'registrations.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=base.now(), attempts=added))+'\n')

    def annotate(rows, registry):
        original_annotate(rows, registry)
        previous.index_repair.annotate(rows)

    def select(cells, retries, active):
        urgent = next((r for r in retries if r['state'] == 'pending'
                       and (r.get('swe_checkpoint_ancestor_repair_of') or r.get('verifier_index_repair_of'))), None)
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
                native_task_containers_restarted=True, other_workers_restarted=False,
                repaired_codex_attempts=0, sweagent_workers_restarted=True,
                repaired_sweagent_attempts=1, deployed_native_source_changed=False,
                host_profile_modified=False, provider_configuration_changed=False,
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
