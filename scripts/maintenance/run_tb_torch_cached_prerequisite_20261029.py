"""Adopt live workers and seed verified public Torch prerequisites in future attempts."""
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_c4_pinned_asset_20261029 as previous

base = previous.base
NAME = 'tb-dual-native-full-20261029-r61-torch-cached-prerequisite'
AUDIT = base.D/'validation/tb-torch-cached-prerequisite-20261029'
PROOF = AUDIT/'cache-integrity.json'
CONTRACT = 'torch-2.7.0-transformers-4.55.0-verified-private-cache/v1'
SOURCE = 'experiments/scripts/tb_prerequisite_cache.py'


def evidence(row):
    if row['task'] != 'torch-pipeline-parallelism' or row['state'] not in base.TERMINAL:
        return None
    path = Path(row['result_path'])
    result = json.loads(path.read_text())
    if (result.get('prerequisite_cache') or result.get('status') != 'verifier_timeout'
            or result.get('official_tests_rc') != 124):
        return None
    diagnosis = AUDIT.parent/'tb-live-monitor-20261026/torch-half-hour-investigation-20261029-1521.json'
    checked = json.loads(diagnosis.read_text())
    if not any(w['harness'] == row['harness'] and w['policy'] == row['policy']
               and any('torch==2.7.0' in u['command'] for u in w['uv'])
               for w in checked['workers']):
        return None
    return dict(classification='verified_public_torch_prerequisites_exceed_verifier_download_deadline',
                result_path=str(path), result_sha256=base.sha(path),
                diagnosis=str(diagnosis), diagnosis_sha256=base.sha(diagnosis),
                cache_validation=str(AUDIT/'result-r3.json'), outcome_filter_used=False,
                original_costs_retained=True)


def register(cells, retries):
    latest = {base.key(r): r for r in cells}
    for row in retries:
        if row['state'] in base.TERMINAL:
            latest[base.key(row)] = row
    for key, row in latest.items():
        history = [r for r in retries if base.key(r) == key]
        if any(r['state'] == 'running' or r.get('torch_cache_repair_cohort') == NAME for r in history):
            continue
        fault = evidence(row)
        if fault is None:
            continue
        first = next(r for r in cells if base.key(r) == key)
        pending = next((r for r in history if r['state'] == 'pending'), None)
        if pending is None:
            pending = dict(task=key[0], harness=key[1], policy=key[2], state='pending',
                           expected_context=first['expected_context'],
                           attempt=max([0]+[r.get('attempt', 1) for r in history])+1,
                           original_state=first['state'], original_result_path=first['result_path'],
                           original_result_sha256=base.sha(first['result_path']))
            retries.append(pending)
        pending.update(torch_cache_repair_cohort=NAME, torch_cache_failure_evidence=fault,
                       previous_attempt_path=row['result_path'],
                       previous_attempt_sha256=base.sha(row['result_path']),
                       retry_reason='one_verified_public_torch_prerequisite_environment_repair')


def fresh_network():
    health = base.prior.health()
    assert health['healthy'] and health['http_status'] == 200
    checks = []
    for harness in ('codex', 'sweagent'):
        proof = json.loads((AUDIT/('native-preflight-'+harness+'-result.json')).read_text())
        path = Path(proof['result_path'])
        result = json.loads(path.read_text())
        assert proof['returncode'] == 0 and proof['no_model_calls']
        assert time.time()-path.stat().st_mtime < 3600
        assert result['status'] == 'preflight_passed' and result['model_calls'] == 0
        assert result['prerequisite_cache']['contract'] == CONTRACT
        assert result['download_network_retry']['host_forwarding_modified'] is False
        assert result['network_baseline']['localhost_resolution']
        checks.append(dict(at=base.now(), harness=harness, no_model_calls=True,
                           result_path=str(path), result_sha256=base.sha(path),
                           network_baseline=result['network_baseline']))
    return dict(at=base.now(), host_nat_unchanged=True, host_forwarding_modified=False,
                private_authenticated_status=health['http_status'], checks=checks,
                no_model_calls=True)


def main():
    validation = json.loads((AUDIT/'result-r3.json').read_text())
    assert validation['returncode'] == 0 and validation['network'] == 'none'
    assert validation['no_model_calls'] and not validation['task_source_mounted']
    assert json.loads((AUDIT/'focused-regression-result.json').read_text())['returncode'] == 0
    staged = AUDIT/'stage'/SOURCE
    assert base.sha(staged) == json.loads((AUDIT/'staged-source.json').read_text())['sha256']
    original = base.main

    def deploy():
        base.NAME, base.ROOT = NAME, base.D/'results'/NAME
        base.PREVIOUS = base.D/'results'/previous.NAME
        base.OLD_UNIT = 'agenttx-tb-dual-full-20261029-r60-c4-pinned-asset.service'
        base.AUDIT = AUDIT/'activation'
        base.FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
        base.REPAIR_FILES = (SOURCE,)
        base.REPAIR_SOURCE_OVERRIDES = {SOURCE: staged}
        base.FRESH_NETWORK_PROOF = fresh_network
        os.environ['AGENTTX_TB_TORCH_PREREQUISITE_CACHE_PROOF'] = str(PROOF)
        old_register, old_save = base.register_shutdown_repairs, base.save

        def register_all(cells, retries):
            old_register(cells, retries)
            register(cells, retries)

        def save(path, data):
            if Path(path) == base.ROOT/'registration.json':
                data = dict(data, coordinator_script=str(Path(__file__)),
                            coordinator_script_sha256=base.sha(__file__), runtime_repair=CONTRACT,
                            torch_prerequisite_cache_proof=str(PROOF),
                            native_task_containers_restarted=False, original_timeout_extended=False,
                            prior_results_immutable=True, cumulative_all_attempt_costs=True,
                            host_8080_forwarding_modified=False, provider_configuration_changed=False,
                            cached_and_uncached_results_separate=True)
                return base.prior.save(path, data)
            old_save(path, data)

        base.register_shutdown_repairs, base.save = register_all, save
        original()

    base.main = deploy
    previous.main()


if __name__ == '__main__':
    main()
