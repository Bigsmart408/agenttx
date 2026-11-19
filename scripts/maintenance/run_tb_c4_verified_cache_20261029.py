"""Adopt live workers and reuse hash-verified C4 assets before range requests."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_private_dev_20261028 as previous

base = previous.base
NAME = 'tb-dual-native-full-20261029-r56-c4-verified-cache'
AUDIT = base.D/'validation/tb-c4-verified-cache-20261028'
CONTRACT = 'c4-verified-cache-before-range-request/v1'
FILES = ('experiments/scripts/bench_tb_image_native.py',
         'experiments/scripts/run_tb_dual_local.py', 'experiments/scripts/tb_verifier_c4_asset.py')


def evidence(row):
    if row['task'] != 'reshard-c4-data' or row['state'] not in base.TERMINAL:
        return None
    p = Path(row['result_path'])
    if not any('/'+name+'/' in str(p) for name in ['tb-dual-native-full-20261028-r54-swe-task-user-submit','tb-dual-native-full-20261028-r55-private-dev']):
        return None
    result = json.loads(p.read_text())
    if result.get('c4_verifier_asset_contract') == CONTRACT:
        return None
    # Apply by the reproduced runner cache defect, independently of task verdict.
    # Do not infer that every failed attempt reached the duplicate range request.
    proof = json.loads((AUDIT/'regression-result.json').read_text())
    assert proof['returncode'] == 0 and proof['no_model_calls'] and proof['no_network_calls']
    return dict(classification='c4_complete_asset_unconditionally_resumed_causes_http416',
                result_path=str(p),result_sha256=base.sha(p),
                validation=str(AUDIT/'regression-result.json'),
                validation_sha256=base.sha(AUDIT/'regression-result.json'),
                outcome_filter_used=False,original_costs_retained=True)


def register(cells, retries):
    latest = {base.key(r):r for r in cells}
    for r in retries:
        if r['state'] in base.TERMINAL:
            latest[base.key(r)] = r
    for key, old in latest.items():
        history = [r for r in retries if base.key(r) == key]
        if any(r['state'] == 'running' or r.get('c4_asset_repair_cohort') == NAME for r in history):
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
        pending.update(c4_asset_repair_of=old['result_path'],c4_asset_repair_cohort=NAME,
                       c4_asset_failure_evidence=proof,previous_attempt_path=old['result_path'],
                       previous_attempt_sha256=base.sha(old['result_path']),
                       retry_reason='one_reproduced_verified_c4_cache_environment_repair')


def main():
    proof = json.loads((AUDIT/'regression-result.json').read_text())
    assert proof['no_model_calls'] and not proof['active_workers_modified']
    assert proof['returncode'] == 0 and proof['no_model_calls'] and proof['no_network_calls']
    assert [(r['case'],r['downloader_calls']) for r in proof['cases']] == [('complete',0),('missing',1),('corrupt',1)]
    sources = json.loads((AUDIT/'staged-sources.json').read_text())
    for row in sources:
        assert base.sha(AUDIT/'stage'/row['path']) == row['sha256']
    original_main = base.main

    def deploy():
        base.NAME,base.ROOT = NAME,base.D/'results'/NAME
        base.PREVIOUS = base.D/'results/tb-dual-native-full-20261028-r55-private-dev'
        base.OLD_UNIT = 'agenttx-tb-dual-full-20261028-r55-private-dev.service'
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
                if fault: row['c4_asset_environment_exclusion']=fault

        def analysis(rows):
            result=old_analysis(rows)
            for row in result:
                if row.get('c4_asset_environment_exclusion'):
                    row['state']='infrastructure_error'
                    row['issue']='c4_complete_asset_unconditionally_resumed_causes_http416'
            return result

        def save(path,data):
            path=Path(path)
            if path==base.ROOT/'registration.json':
                data=dict(data,coordinator_script=str(Path(__file__)),coordinator_script_sha256=base.sha(__file__),
                          runtime_repair=CONTRACT,c4_asset_validation=str(AUDIT/'regression-result.json'),
                          original_frozen_worker_sources_unchanged=False,
                          native_task_containers_restarted=False,other_workers_restarted=False,
                          repaired_tasks=['reshard-c4-data'],outcome_based_retries_added=False,
                          host_8080_forwarding_modified=False,provider_configuration_changed=False,
                          original_timeout_extended=False,prior_results_immutable=True,
                          cumulative_all_attempt_costs=True,download_proxy_default_enabled=True)
                return base.prior.save(path,data)
            if path==base.ROOT/'retry-report.json':
                data['c4_asset_repair']=dict(contract=CONTRACT,validation=str(AUDIT/'regression-result.json'),
                    raw_official_outcomes_unchanged=True,all_costs_retained=True,mixed_contracts_not_paired=True)
            old_save(path,data)

        base.register_shutdown_repairs,base.annotate = register_all,annotate
        base.analysis_rows,base.save = analysis,save
        original_main()

    base.main=deploy
    previous.main()


if __name__=='__main__':
    main()
