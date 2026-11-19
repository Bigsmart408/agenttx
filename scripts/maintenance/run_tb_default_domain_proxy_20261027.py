#!/usr/bin/env python3
"""Adopt live work; use the user's domain-scoped proxy for every new slot."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_recovered_connection_20261027 as base

NAME = 'tb-dual-native-full-20261027-r38-default-domain-proxy'
AUDIT = base.D/'validation/tb-default-domain-proxy-20261027/activation'


def apply_interruption(cells, retries, proof):
    """Preserve the exact interrupted bill and create one transport replacement."""
    assert proof['user_authorized_immediate_proxy'] and proof['worker_stopped']
    assert proof['network_failure_evidence']['actual_tool_observation']
    source = Path(proof['network_failure_evidence']['path'])
    assert base.sha(source) == proof['network_failure_evidence']['sha256']
    assert base.sha(proof['result_path']) == proof['terminal_result_sha256']
    old = next(r for r in cells+retries if r.get('result_path') == proof['result_path'])
    assert old['state'] == 'running' and old['worker_pid'] == proof['worker_pid']
    history = [r for r in retries if base.key(r) == base.key(old)]
    assert not any(r.get('resumed_user_interruption_of') == old['result_path'] for r in history)
    assert not any(r['state'] == 'pending' for r in history)
    original = next(r for r in cells if base.key(r) == base.key(old))
    old.update(state='infrastructure_error', issue='user_authorized_immediate_selective_proxy',
               finished_at=proof['finished_at'], worker_returncode=proof['worker_returncode'],
               interrupted_at_user_request=True, user_scheduling_interruption=proof)
    retries.append(dict(task=old['task'], harness=old['harness'], policy=old['policy'],
        expected_context=old['expected_context'], state='pending',
        attempt=max([0]+[r.get('attempt',1) for r in history])+1,
        original_state=original['state'], original_result_path=original['result_path'],
        original_result_sha256=base.sha(original['result_path']),
        previous_attempt_path=old['result_path'], previous_attempt_sha256=base.sha(old['result_path']),
        resumed_user_interruption_of=old['result_path'], download_proxy_retry=True,
        download_retry_cohort=base.DOWNLOAD_REPAIR_COHORT,
        download_retry_of=old['result_path'], download_failure_evidence=proof['network_failure_evidence'],
        retry_reason='user_requested_immediate_domain_scoped_proxy_after_actual_hf_network_retries'))


def select(cells, retries, active):
    # An actual download retry is urgent; ordinary retries remain balanced.
    urgent = next((r for r in retries if r['state']=='pending' and r.get('download_proxy_retry')), None)
    if urgent:
        return urgent, True
    return base.prior.old.select(cells, retries, active)


def main():
    wrapper_path = Path(__file__)
    base.NAME = NAME
    base.ROOT = base.D/'results'/NAME
    base.PREVIOUS = base.D/'results/tb-dual-native-full-20261027-r37-recovered-connection'
    base.AUDIT = AUDIT
    base.OLD_UNIT = 'agenttx-tb-dual-full-20261027-r37-recovered-connection.service'
    base.FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
    base.REPAIR_FILES = ('experiments/scripts/tb_download_proxy.py',)
    prior_register = base.register_shutdown_repairs
    applied = False

    def register(cells, retries):
        nonlocal applied
        if not applied:
            proof = json.loads((AUDIT.parent/'enforcement.json').read_text())
            apply_interruption(cells, retries, proof)
            applied = True
        prior_register(cells, retries)

    original_save = base.save

    def save(path, data):
        if Path(path) == base.ROOT/'registration.json':
            data = dict(data, coordinator_script=str(wrapper_path),
                coordinator_script_sha256=base.sha(wrapper_path),
                coordinator_base_script_sha256=base.sha(base.PROJECT/'scripts/maintenance/run_tb_recovered_connection_20261027.py'),
                runtime_repair='default-github-hf-selective-proxy; one-immediate-hf-interruption/v1',
                user_instruction='八路GitHub/Hugging Face走代理，其他DIRECT；明确网络重试立即切换，8080不动',
                download_proxy_default_enabled=True,
                download_retry_transport='Every new slot: private-netns Mihomo; GitHub/HF only; all other domains and port 8080 DIRECT',
                original_frozen_worker_sources_unchanged=False,
                native_task_containers_restarted=True,
                old_attempts_interrupted_at_user_request=True,
                existing_network_task_workers_finish_without_restart=True,
                other_workers_restarted=False,
                interruption_evidence=str(AUDIT.parent/'enforcement.json'),
                proxy_default_ordinary_dispatch_is_not_failure_retry=True)
        if Path(path).name == 'progress.json' and Path(path).parent == base.ROOT:
            rows = data['tasks'] + data['retry_tasks']
            data['download_retry']['marked'] = sum(bool(r.get('download_proxy_retry')) for r in rows)
            data['download_retry']['running'] = sum(r['state']=='running' and bool(r.get('download_proxy_retry')) for r in rows)
            data['download_transport_default'] = dict(enabled=True, domains='GitHub/HuggingFace',
                other_domains='DIRECT', port_8080='DIRECT', adopted_workers_keep_original_environment=True)
        if Path(path) == base.ROOT/'retry-report.json':
            data['download_proxy_default_enabled'] = True
            data['ordinary_dispatch_proxy_is_not_an_additional_retry'] = True
        original_save(path, data)

    base.register_shutdown_repairs = register
    base.select = select
    base.requires_proxy = lambda row: True
    base.save = save
    base.main()


if __name__ == '__main__':
    main()
