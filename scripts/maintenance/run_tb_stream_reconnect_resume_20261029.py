"""Resume a drained cohort after proving a stream reconnect completed normally."""
import json
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_c4_verified_cache_20261029 as previous

base = previous.base
NAME = 'tb-dual-native-full-20261029-r57-reviewed-stream-reconnect'
AUDIT = base.D/'validation/tb-stream-reconnect-resume-20261029'
OLD = base.D/'results'/previous.NAME


def review_halt(progress):
    halt_path = previous.AUDIT/'activation/dispatch-halt.json'
    halt = json.loads(halt_path.read_text())
    row = next(r for r in progress['tasks']+progress['retry_tasks']
               if r.get('result_path') == halt['row']['result_path'])
    proof = base.recovered_connection_evidence(row)
    assert proof and proof['usage_complete'] and proof['model_returncode'] == 0
    assert base.key(row) == ('slurm-simple-node-monitoring', 'codex', 'temporal_checkpoint')
    return dict(proof, reviewed_environment_repair=True,
                dispatch_halt_sha256=base.sha(halt_path),
                classifier_sha256=base.sha(Path(__file__).with_name('tb_recovered_connection_20261027.py')),
                official_outcome_changed=False, extra_retry_added=False)


def review_dead(pointer):
    assert pointer['cohort'] == previous.NAME
    assert base.prior.state(pointer['coordinator_pid']) in (None, 'Z')
    progress = json.loads((OLD/'progress.json').read_text())
    execution = json.loads((OLD/'execution.json').read_text())
    assert not progress['active']
    assert not any(r['state']=='running' for r in progress['tasks']+progress['retry_tasks'])
    assert execution['stop_reason'] == progress['stop_reason'] == 'new_environment_exception_requires_review'
    assert execution['first_attempt_counts'] == progress['counts']
    status = subprocess.check_output(['systemctl','--user','show',base.OLD_UNIT,
        '-p','LoadState','-p','MainPID'], text=True)
    assert 'LoadState=not-found' in status.splitlines() and 'MainPID=0' in status.splitlines()
    return dict(coordinator_dead=True, no_live_workers_and_unit_collected=True,
                execution_path=str(OLD/'execution.json'), execution_sha256=base.sha(OLD/'execution.json'),
                unit_status=status, root_cause='drained after recovered stream notice misclassification')


def fresh_network():
    health = base.prior.health()
    assert health['healthy'] and health['http_status'] == 200
    checks=[]
    frozen=Path(json.loads((OLD/'registration.json').read_text())['frozen_source'])
    evidence=str(base.D/'validation/tb-basic-official-validation-20261019-r1/filter-js-from-html/result.json')
    for harness in ('codex','sweagent'):
        folder=AUDIT/('preflight-'+harness)
        command=base.cell_command(frozen,folder,dict(task='filter-js-from-html',harness=harness,policy='clean_recovery'),evidence)+['--preflight-only']
        env=dict(os.environ,AGENTTX_DOWNLOAD_PROXY_RETRY='1',AGENTTX_NO_PROXY='1',NO_PROXY='*',AGENTTX_SKIP_ORPHAN_REAP='1')
        with (AUDIT/('preflight-'+harness+'.log')).open('w') as log:
            rc=subprocess.run(command,cwd=frozen,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=240).returncode
        path=folder/'clean_recovery/host-result.json'
        result=json.loads(path.read_text())
        proxy=result['download_network_retry']
        assert rc==0 and result['status']=='preflight_passed' and result['model_calls']==0
        assert proxy['model_api_bypasses_local_proxy'] and proxy['host_forwarding_modified'] is False
        assert proxy['network'] != 'host' and result['network_baseline']['localhost_resolution']
        checks.append(dict(at=base.now(),harness=harness,no_model_calls=True,
                           result_path=str(path),result_sha256=base.sha(path),network_baseline=result['network_baseline']))
    proof=dict(at=base.now(),host_nat_unchanged=True,host_forwarding_modified=False,
               proof_scope='preflight adapters report private networking and no host forwarding mutations; no host NAT commands executed',
               private_authenticated_status=health['http_status'],checks=checks,no_model_calls=True)
    base.save(AUDIT/'fresh-network-preflight.json',proof)
    return proof


def main():
    AUDIT.mkdir(parents=True,exist_ok=True)
    original=base.main

    def deploy():
        base.NAME,base.ROOT=NAME,base.D/'results'/NAME
        base.PREVIOUS=OLD
        base.OLD_UNIT='agenttx-tb-dual-full-20261029-r56-c4-verified-cache.service'
        base.AUDIT=AUDIT/'activation'
        base.FROZEN=Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
        base.REPAIR_FILES=()
        base.REPAIR_SOURCE_OVERRIDES={}
        base.REVIEW_INHERITED_HALT=review_halt
        base.REVIEW_DEAD_COORDINATOR=review_dead
        base.FRESH_NETWORK_PROOF=fresh_network
        old_save=base.save

        def save(path,data):
            if Path(path)==base.ROOT/'registration.json':
                data=dict(data,coordinator_script=str(Path(__file__)),coordinator_script_sha256=base.sha(__file__),
                          runtime_repair='reviewed-completed-stream-reconnect/v1',
                          original_frozen_worker_sources_unchanged=True,
                          additional_outcome_based_retries=False)
            old_save(path,data)
        base.save=save
        original()

    base.main=deploy
    previous.main()


if __name__=='__main__':
    main()
