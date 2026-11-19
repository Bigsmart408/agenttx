"""Preserve the verifier-selected OSMesa backend as well as scoped downloads."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_sudo_osmesa_input_20261029 as previous

base = previous.base
sys.path.insert(0, str(base.PROJECT))
from experiments.scripts.tb_sudo_download_proxy import stripped_environment_failure
NAME = 'tb-dual-native-full-20261029-r65-sudo-render-failure-classification'
AUDIT = base.D/'validation/tb-sudo-render-classification-20261029'
CONTRACT = 'private-task-sudo-preserves-download-proxy-and-osmesa-input/v2'
FILES = ()


def evidence(row):
    if row['task'] != 'unprivileged-headless-pyrender' or row['state'] not in base.TERMINAL:
        return None
    path = Path(row['result_path'])
    result = json.loads(path.read_text())
    if result.get('sudo_download_proxy_contract') == CONTRACT or result.get('official_success'):
        return None
    log = path.parent/'verifier.stdout.log'
    text = log.read_text(errors='replace') if log.exists() else ''
    reason = stripped_environment_failure(text)
    if not reason:
        return None
    return dict(classification='official-render-sudo-stripped-proxy-or-explicit-osmesa-input',
                reason=reason, classifier_sha256=base.sha(base.PROJECT/'experiments/scripts/tb_sudo_download_proxy.py'),
                result_path=str(path), result_sha256=base.sha(path),
                verifier_log=str(log), verifier_sha256=base.sha(log),
                reproduction=str(AUDIT/'sudo-proxy-integration.json'),
                reproduction_sha256=base.sha(AUDIT/'sudo-proxy-integration.json'),
                original_costs_retained=True, outcome_direction_filter=False)


def register(cells, retries):
    latest = {base.key(r): r for r in cells}
    for row in retries:
        if row['state'] in base.TERMINAL:
            latest[base.key(row)] = row
    for key, row in latest.items():
        history = [r for r in retries if base.key(r) == key]
        if any(r['state'] == 'running' or r.get('sudo_proxy_repair_cohort') == NAME for r in history):
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
        pending.update(sudo_proxy_repair_cohort=NAME, sudo_proxy_failure_evidence=fault,
                       previous_attempt_path=row['result_path'],
                       previous_attempt_sha256=base.sha(row['result_path']),
                       retry_reason='one_verified_sudo_proxy_and_osmesa_input_environment_repair')


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
        assert result['sudo_download_proxy_contract'] == CONTRACT
        assert result['download_network_retry']['host_forwarding_modified'] is False
        assert result['network_baseline']['localhost_resolution']
        checks.append(dict(at=base.now(), harness=harness, no_model_calls=True,
                           result_path=str(path), result_sha256=base.sha(path),
                           network_baseline=result['network_baseline']))
    proof = dict(at=base.now(), host_nat_unchanged=True, host_forwarding_modified=False,
                 private_authenticated_status=health['http_status'], checks=checks,
                 no_model_calls=True)
    (AUDIT/'handoff-network-validation.json').write_text(json.dumps(proof, indent=2)+'\n')
    return proof


def main():
    reproduction = json.loads((AUDIT/'sudo-proxy-integration.json').read_text())
    assert reproduction['no_model_calls'] and not reproduction['host_forwarding_modified']
    assert reproduction['public_asset_fetch']['returncode'] == 0
    assert json.loads((AUDIT/'focused-regression-result.json').read_text())['returncode'] == 0
    original = base.main

    def deploy():
        base.NAME, base.ROOT = NAME, base.D/'results'/NAME
        base.PREVIOUS = base.D/'results/tb-dual-native-full-20261029-r64-sudo-domain-proxy-osmesa-input'
        base.OLD_UNIT = 'agenttx-tb-dual-full-20261029-r64-sudo-domain-proxy-osmesa-input.service'
        base.AUDIT = AUDIT/'activation'
        base.FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
        base.REPAIR_FILES = FILES
        stage = Path('/home/bfq/agenttx-workspace/agenttx-executions/tb-sudo-osmesa-preflight-20261029/project')
        base.REPAIR_SOURCE_OVERRIDES = {name: stage/name for name in FILES}
        base.FRESH_NETWORK_PROOF = fresh_network
        old_register, old_save = base.register_shutdown_repairs, base.save

        def register_all(cells, retries):
            old_register(cells, retries)
            register(cells, retries)

        def save(path, data):
            if Path(path) == base.ROOT/'registration.json':
                data = dict(data, coordinator_script=str(Path(__file__)),
                            coordinator_script_sha256=base.sha(__file__), runtime_repair=CONTRACT,
                            environment_failure_classifier_sha256=base.sha(base.PROJECT/'experiments/scripts/tb_sudo_download_proxy.py'),
                            native_task_containers_restarted=False,
                            original_timeout_extended=False, prior_results_immutable=True,
                            cumulative_all_attempt_costs=True, host_8080_forwarding_modified=False,
                            provider_configuration_changed=False,
                            repaired_and_original_configurations_separate=True)
                return base.prior.save(path, data)
            old_save(path, data)

        base.register_shutdown_repairs, base.save = register_all, save
        original()

    base.main = deploy
    previous.main()


if __name__ == '__main__':
    main()
