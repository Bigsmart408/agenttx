"""Adopt active workers and hide C4 coordinator backing storage from task views."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_stream_reconnect_resume_20261029 as previous

base = previous.base
NAME = 'tb-dual-native-full-20261029-r58-session-visibility'
AUDIT = base.D/'validation/tb-session-visibility-20261029'
CONTRACT = 'coordinator-session-hidden-execution-scripts-readonly/v1'
FILES = ('experiments/scripts/bench_tb_image_native.py',
         'experiments/scripts/run_tb_dual_local.py', 'scripts/patch_try_session_visibility.py')


def evidence(row):
    if row['task'] != 'reshard-c4-data' or row['state'] not in base.TERMINAL:
        return None
    path = Path(row['result_path'])
    result = json.loads(path.read_text())
    if result.get('session_visibility_contract') == CONTRACT:
        return None
    proof_path = AUDIT/'latest-result.json'
    proof = json.loads(proof_path.read_text())
    fixed = json.loads(next(r['stdout'] for r in proof['results'] if r['mode']=='fixed'))
    assert fixed['snapshot']['official_output_check_passed']
    assert fixed['snapshot']['real_outside_write_rejected']
    return dict(classification='coordinator_backing_storage_visible_to_official_filesystem_snapshot',
                result_path=str(path), result_sha256=base.sha(path),
                validation=str(proof_path), validation_sha256=base.sha(proof_path),
                reproduced_alias_proof=str(base.D/'validation/tb-live-monitor-20261026/reshard-output-backing-alias-proof-20261029-0032.json'),
                outcome_filter_used=False, original_costs_retained=True)


def register(cells, retries):
    latest = {base.key(r):r for r in cells}
    for r in retries:
        if r['state'] in base.TERMINAL:latest[base.key(r)] = r
    for key, old in latest.items():
        history = [r for r in retries if base.key(r)==key]
        if any(r['state']=='running' or r.get('session_visibility_repair_cohort')==NAME for r in history):
            continue
        proof = evidence(old)
        if proof is None:continue
        first = next(r for r in cells if base.key(r)==key)
        pending = next((r for r in history if r['state']=='pending'),None)
        if pending is None:
            pending = dict(task=key[0],harness=key[1],policy=key[2],state='pending',
                           expected_context=first['expected_context'],
                           attempt=max([0]+[r.get('attempt',1) for r in history])+1,
                           original_state=first['state'],original_result_path=first['result_path'],
                           original_result_sha256=base.sha(first['result_path']))
            retries.append(pending)
        pending.update(session_visibility_repair_of=old['result_path'],
                       session_visibility_repair_cohort=NAME,session_visibility_failure_evidence=proof,
                       previous_attempt_path=old['result_path'],previous_attempt_sha256=base.sha(old['result_path']),
                       retry_reason='one_reproduced_coordinator_session_visibility_environment_repair')


def fresh_network():
    health = base.prior.health()
    assert health['healthy'] and health['http_status']==200
    checks=[]
    for harness in ('codex','sweagent'):
        proof_path=AUDIT/('adapter-preflight-'+harness+'-result.json')
        proof=json.loads(proof_path.read_text())
        assert proof['returncode']==0 and proof['no_model_calls']
        result_path=Path(proof['result_path'])
        assert base.sha(result_path)==proof['result_sha256'] and time.time()-result_path.stat().st_mtime<3600
        result=json.loads(result_path.read_text());proxy=result['download_network_retry']
        assert result['model_calls']==0 and result['session_visibility_contract']==CONTRACT
        assert not proxy['host_forwarding_modified'] and proxy['model_api_bypasses_local_proxy']
        assert proxy['network']!='host' and result['network_baseline']['localhost_resolution']
        checks.append(dict(at=base.now(),harness=harness,no_model_calls=True,
                           result_path=str(result_path),result_sha256=base.sha(result_path),
                           network_baseline=result['network_baseline']))
    return dict(at=base.now(),host_nat_unchanged=True,host_forwarding_modified=False,
                proof_scope='fresh private adapter preflights; no host NAT commands executed',
                private_authenticated_status=health['http_status'],checks=checks,no_model_calls=True)


def main():
    proof=json.loads((AUDIT/'latest-result.json').read_text())
    assert proof['no_model_calls'] and not proof['active_workers_modified'] and not proof['host_forwarding_modified']
    assert all(r['returncode']==0 for r in proof['results'])
    fixed=json.loads(next(r['stdout'] for r in proof['results'] if r['mode']=='fixed'))
    assert fixed['snapshot']['official_snapshot_completed'] and fixed['snapshot']['official_output_check_passed']
    assert fixed['snapshot']['real_outside_write_rejected'] and fixed['rollback_ok'] and fixed['fork_ok']
    for row in json.loads((AUDIT/'staged-sources.json').read_text()):
        assert base.sha(AUDIT/'stage'/row['path'])==row['sha256']
    original=base.main

    def deploy():
        base.NAME,base.ROOT=NAME,base.D/'results'/NAME
        base.PREVIOUS=base.D/'results'/previous.NAME
        base.OLD_UNIT='agenttx-tb-dual-full-20261029-r57-reviewed-stream-reconnect.service'
        base.AUDIT=AUDIT/'activation'
        base.FROZEN=Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
        base.REPAIR_FILES=FILES
        base.REPAIR_SOURCE_OVERRIDES={p:AUDIT/'stage'/p for p in FILES}
        base.REVIEW_INHERITED_HALT=None
        base.REVIEW_DEAD_COORDINATOR=None
        base.FRESH_NETWORK_PROOF=fresh_network
        old_register,old_annotate,old_analysis,old_save=base.register_shutdown_repairs,base.annotate,base.analysis_rows,base.save

        def register_all(cells,retries):
            old_register(cells,retries)
            register(cells,retries)

        def annotate(rows,registry):
            old_annotate(rows,registry)
            for row in rows:
                fault=evidence(row)
                if fault:row['session_visibility_environment_exclusion']=fault

        def analysis(rows):
            result=old_analysis(rows)
            for row in result:
                if row.get('session_visibility_environment_exclusion'):
                    row['state']='infrastructure_error'
                    row['issue']='coordinator_backing_storage_visible_to_official_filesystem_snapshot'
            return result

        def save(path,data):
            if Path(path)==base.ROOT/'registration.json':
                data=dict(data,coordinator_script=str(Path(__file__)),coordinator_script_sha256=base.sha(__file__),
                          runtime_repair=CONTRACT,session_visibility_validation=str(AUDIT/'latest-result.json'),
                          original_frozen_worker_sources_unchanged=False,native_task_containers_restarted=False,
                          repaired_tasks=['reshard-c4-data'],outcome_based_retries_added=False,
                          original_timeout_extended=False,prior_results_immutable=True,
                          cumulative_all_attempt_costs=True,host_8080_forwarding_modified=False,
                          provider_configuration_changed=False)
                return base.prior.save(path,data)
            if Path(path)==base.ROOT/'retry-report.json':
                data['session_visibility_repair']=dict(contract=CONTRACT,validation=str(AUDIT/'latest-result.json'),
                    raw_official_outcomes_unchanged=True,all_costs_retained=True,mixed_contracts_not_paired=True)
            old_save(path,data)

        base.register_shutdown_repairs,base.annotate=register_all,annotate
        base.analysis_rows,base.save=analysis,save
        original()

    base.main=deploy
    previous.main()


if __name__=='__main__':
    main()
