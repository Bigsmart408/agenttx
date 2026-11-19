"""Restore only pinned WASM fixture inputs; adopt live workers and retain all fees."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parent))
import run_tb_reviewed_flink_connection_timeout_20261030 as previous
base=previous.base
NAME='tb-dual-native-full-20261030-r70-wasm-reference-inputs'
AUDIT=base.D/'validation/tb-wasm-reference-inputs-20261030'
CONTRACT='restore-pinned-wasm-input-pair-before-each-verifier/v1'
FILES=('experiments/scripts/bench_tb_image_native.py',)


def evidence(row):
    if row['task']!='wasm-pipeline' or row['state'] not in base.TERMINAL:return None
    path=Path(row['result_path']);result=json.loads(path.read_text())
    if result.get('reference_input_contract')==CONTRACT or result.get('official_success') is not False:return None
    if result.get('model_returncode')!=0 or not result.get('model_finished'):return None
    log=path.parent/'verifier.stdout.log'
    text=log.read_text(errors='replace') if log.exists() else ''
    required=('FAILED ../tests/test_outputs.py::test_pipeline_runs_with_sample_files',
              'PASSED ../tests/test_outputs.py::test_pipeline_runs_with_random_files_and_produces_expected_output',
              'E         + -108866406211860134')
    if not all(s in text for s in required):return None
    return dict(classification='official_random_test_overwrote_wasm_sample_input_pair',
                result_path=str(path),result_sha256=base.sha(path),verifier_log=str(log),
                verifier_sha256=base.sha(log),validation=str(AUDIT/'validation.json'),
                validation_sha256=base.sha(AUDIT/'validation.json'),prior_costs_retained=True,
                original_tests_or_candidate_modified=False)


def register(cells,retries):
    latest={base.key(r):r for r in cells}
    for r in retries:
        if r['state'] in base.TERMINAL:latest[base.key(r)]=r
    for key,row in latest.items():
        history=[r for r in retries if base.key(r)==key]
        if any(r['state']=='running' or r.get('wasm_input_repair_cohort')==NAME for r in history):continue
        proof=evidence(row)
        if proof is None:continue
        first=next(r for r in cells if base.key(r)==key)
        pending=next((r for r in history if r['state']=='pending'),None)
        if pending is None:
            pending=dict(task=key[0],harness=key[1],policy=key[2],state='pending',expected_context=first['expected_context'],
                         attempt=max([0]+[r.get('attempt',1) for r in history])+1,
                         original_state=first['state'],original_result_path=first['result_path'],
                         original_result_sha256=base.sha(first['result_path']))
            retries.append(pending)
        pending.update(wasm_input_repair_cohort=NAME,wasm_input_failure_evidence=proof,
                       previous_attempt_path=row['result_path'],previous_attempt_sha256=base.sha(row['result_path']),
                       retry_reason='one_verified_official_random_test_input_residue_repair')


def fresh_network():
    health=base.prior.health();assert health['healthy'] and health['http_status']==200
    checks=[]
    for h in ('codex','sweagent'):
        proof=json.loads((AUDIT/('native-'+h+'-result.json')).read_text());path=Path(proof['result_path']);r=json.loads(path.read_text())
        assert proof['passed'] and proof['no_model_calls'] and base.sha(path)==proof['result_sha256']
        assert r['reference_input_contract']==CONTRACT and r['model_calls']==0
        assert not r['download_network_retry']['host_forwarding_modified']
        checks.append(dict(at=base.now(),harness=h,no_model_calls=True,result_path=str(path),
                           result_sha256=base.sha(path),network_baseline=r['network_baseline']))
    return dict(at=base.now(),host_nat_unchanged=True,host_forwarding_modified=False,
                private_authenticated_status=200,checks=checks,no_model_calls=True,
                proof_scope='native private-network adapter preflights; no host NAT commands')


def main():
    proof=json.loads((AUDIT/'validation.json').read_text());assert proof['passed'] and proof['no_model_calls']
    assert proof['focused_tests']['returncode']==0
    for row in proof['sources']:assert base.sha(base.PROJECT/row['path'])==row['sha256']
    original=base.main
    def deploy():
        base.NAME,base.ROOT=NAME,base.D/'results'/NAME
        base.PREVIOUS=base.D/'results'/previous.NAME
        base.OLD_UNIT='agenttx-tb-dual-full-20261029-r69-reviewed-flink-connection-timeout.service'
        base.AUDIT=AUDIT/'activation';base.FROZEN=Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
        base.REPAIR_FILES=FILES;base.REPAIR_SOURCE_OVERRIDES={p:Path(proof['stage'])/p for p in FILES}
        base.FRESH_NETWORK_PROOF=fresh_network
        old_register,old_save=base.register_shutdown_repairs,base.save
        def register_all(cells,retries):old_register(cells,retries);register(cells,retries)
        def save(path,data):
            if Path(path)==base.ROOT/'registration.json':
                data=dict(data,coordinator_script=str(Path(__file__)),coordinator_script_sha256=base.sha(__file__),
                          runtime_repair=CONTRACT,wasm_reference_validation=str(AUDIT/'validation.json'),
                          candidate_and_official_tests_unchanged=True,prior_results_immutable=True,
                          cumulative_all_attempt_costs=True,existing_workers_restarted=False)
                return base.prior.save(path,data)
            old_save(path,data)
        base.register_shutdown_repairs,base.save=register_all,save
        original()
    base.main=deploy
    previous.main()
if __name__=='__main__':main()
