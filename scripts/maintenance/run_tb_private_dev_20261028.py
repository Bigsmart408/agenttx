"""Adopt live workers and repair the reproduced C4 verifier terminal alias."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_swe_task_user_submit_20261028 as previous

base = previous.base
NAME = 'tb-dual-native-full-20261028-r55-private-dev'
AUDIT = base.D/'validation/tb-private-dev-20261028'
CONTRACT = 'private-dev-tmpfs-no-backing-terminal-alias/v1'
FILES = ('experiments/scripts/bench_tb_image_native.py',
         'experiments/scripts/run_tb_dual_local.py', 'scripts/patch_try_private_dev.py')


def evidence(row):
    if row['task'] != 'reshard-c4-data' or row['state'] not in base.TERMINAL:
        return None
    p = Path(row['result_path'])
    if '/tb-dual-native-full-20261028-r54-swe-task-user-submit/' not in str(p):
        return None
    result = json.loads(p.read_text())
    if result.get('device_namespace_contract') == CONTRACT:
        return None
    # Apply by the reproduced runner defect, independently of task verdict.
    # Do not infer that every failed attempt reached the blocking read.
    proof = json.loads((AUDIT/'latest-result.json').read_text())
    assert all(r['returncode'] == 0 for r in proof['results'])
    return dict(classification='c4_snapshot_can_read_runner_backing_terminal_alias',
                result_path=str(p),result_sha256=base.sha(p),
                validation=str(AUDIT/'latest-result.json'),
                validation_sha256=base.sha(AUDIT/'latest-result.json'),
                outcome_filter_used=False,original_costs_retained=True)


def register(cells, retries):
    latest = {base.key(r):r for r in cells}
    for r in retries:
        if r['state'] in base.TERMINAL:
            latest[base.key(r)] = r
    for key, old in latest.items():
        history = [r for r in retries if base.key(r) == key]
        if any(r['state'] == 'running' or r.get('private_dev_repair_cohort') == NAME for r in history):
            continue
        proof = evidence(old)
        if proof is None:
            continue
        first = next(r for r in cells if base.key(r) == key)
        pending = next((r for r in history if r['state'] == 'pending'),None)
        if pending is None:
            pending = dict(task=key[0],harness=key[1],policy=key[2],state='pending',
                           expected_context=first['expected_context'],
                           attempt=max([0]+[r.get('attempt',1) for r in history])+1,
                           original_state=first['state'],original_result_path=first['result_path'],
                           original_result_sha256=base.sha(first['result_path']))
            retries.append(pending)
        pending.update(private_dev_repair_of=old['result_path'],private_dev_repair_cohort=NAME,
                       private_dev_failure_evidence=proof,previous_attempt_path=old['result_path'],
                       previous_attempt_sha256=base.sha(old['result_path']),
                       retry_reason='one_reproduced_runner_device_namespace_repair')


def main():
    proof = json.loads((AUDIT/'latest-result.json').read_text())
    assert proof['no_model_calls'] and not proof['active_workers_modified']
    assert all(r['returncode'] == 0 for r in proof['results'])
    fixed = json.loads(next(r['stdout'] for r in proof['results'] if r['mode']=='fixed'))
    assert fixed['snapshot']['official_snapshot_completed'] and fixed['rollback_ok'] and fixed['fork_ok']
    sources = json.loads((AUDIT/'staged-sources.json').read_text())
    for row in sources:
        assert base.sha(AUDIT/'stage'/row['path']) == row['sha256']
    original_main = base.main

    def deploy():
        base.NAME,base.ROOT = NAME,base.D/'results'/NAME
        base.PREVIOUS = base.D/'results/tb-dual-native-full-20261028-r54-swe-task-user-submit'
        base.OLD_UNIT = 'agenttx-tb-dual-full-20261028-r54-swe-task-user-submit.service'
        base.AUDIT,base.FROZEN = AUDIT/'activation',Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
        base.REPAIR_FILES = FILES
        base.REPAIR_SOURCE_OVERRIDES = {p:AUDIT/'stage'/p for p in FILES}
        base.REVIEW_INHERITED_HALT = None
        old_register,old_annotate,old_analysis,old_save = base.register_shutdown_repairs,base.annotate,base.analysis_rows,base.save

        def register_all(cells,retries):
            old_register(cells,retries)
            register(cells,retries)

        def annotate(rows,registry):
            old_annotate(rows,registry)
            for row in rows:
                fault=evidence(row)
                if fault: row['private_dev_environment_exclusion']=fault

        def analysis(rows):
            result=old_analysis(rows)
            for row in result:
                if row.get('private_dev_environment_exclusion'):
                    row['state']='infrastructure_error'
                    row['issue']='c4_snapshot_can_read_runner_backing_terminal_alias'
            return result

        def save(path,data):
            path=Path(path)
            if path==base.ROOT/'registration.json':
                data=dict(data,coordinator_script=str(Path(__file__)),coordinator_script_sha256=base.sha(__file__),
                          runtime_repair=CONTRACT,device_namespace_validation=str(AUDIT/'latest-result.json'),
                          original_frozen_worker_sources_unchanged=False,
                          native_task_containers_restarted=False,other_workers_restarted=False,
                          repaired_tasks=['reshard-c4-data'],outcome_based_retries_added=False,
                          host_8080_forwarding_modified=False,provider_configuration_changed=False,
                          original_timeout_extended=False,prior_results_immutable=True,
                          cumulative_all_attempt_costs=True,download_proxy_default_enabled=True)
                return base.prior.save(path,data)
            if path==base.ROOT/'retry-report.json':
                data['private_dev_repair']=dict(contract=CONTRACT,validation=str(AUDIT/'latest-result.json'),
                    raw_official_outcomes_unchanged=True,all_costs_retained=True,mixed_contracts_not_paired=True)
            old_save(path,data)

        base.register_shutdown_repairs,base.annotate = register_all,annotate
        base.analysis_rows,base.save = analysis,save
        original_main()

    base.main=deploy
    previous.main()


if __name__=='__main__':
    main()
