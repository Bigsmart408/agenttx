#!/usr/bin/env python3
"""Adopt live workers and give future dependency downloads a bounded 180s timeout."""
import collections
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_recovered_connection_20261027 as base
import run_tb_hf_cache_lifecycle_20261027 as cache_repair

NAME = 'tb-dual-native-full-20261027-r41-verifier-download-timeout'
AUDIT = base.D/'validation/tb-verifier-download-timeout-repair-20261027'
CONTRACT = 'uv-agent-and-verifier-read-timeout-180s/v2'


def timeout_fault(row):
    if row['state'] not in base.TERMINAL:return None
    path=Path(row['result_path']); result=json.loads(path.read_text())
    if result.get('success') or result.get('download_timeout_contract')==CONTRACT:return None
    if result.get('official_tests_rc')!=2 or result.get('model_returncode')!=0:return None
    log=path.parent/'verifier.stderr.log'
    if not log.is_file():return None
    text=log.read_text(errors='replace')
    if not all(s in text for s in ('Failed to download distribution due to network timeout',
                                   'UV_HTTP_TIMEOUT (current value: 40s)',
                                   'Failed to spawn: `pytest`')):return None
    return dict(classification='verifier_dependency_uv_40s_read_timeout_before_pytest',
        result_path=str(path),result_sha256=base.sha(path),
        verifier_stderr_path=str(log),verifier_stderr_sha256=base.sha(log),
        prior_uv_http_timeout_s=40,repaired_uv_http_timeout_s=180)


def register_timeout_repairs(cells,retries):
    latest={base.key(r):r for r in cells}
    for row in retries:
        if row['state'] in base.TERMINAL:latest[base.key(row)]=row
    added=[]
    for k,old in latest.items():
        history=[r for r in retries if base.key(r)==k]
        if any(r['state']=='running' or (r.get('download_timeout_repair_of') and r.get('download_timeout_repair_cohort')==NAME) for r in history):continue
        proof=timeout_fault(old)
        if proof is None:continue
        first=next(r for r in cells if base.key(r)==k)
        pending=next((r for r in history if r['state']=='pending'),None)
        if pending is None:
            pending=dict(task=k[0],harness=k[1],policy=k[2],expected_context=first['expected_context'],
                state='pending',attempt=max([0]+[r.get('attempt',1) for r in history])+1,
                original_state=first['state'],original_result_path=first['result_path'],
                original_result_sha256=base.sha(first['result_path']))
            retries.append(pending)
        pending.update(download_timeout_repair_of=old['result_path'],download_timeout_repair_cohort=NAME,
            download_timeout_failure_evidence=proof,previous_attempt_path=old['result_path'],
            previous_attempt_sha256=base.sha(old['result_path']),
            retry_reason='one_evidenced_uv_dependency_download_timeout_environment_repair')
        added.append(pending)
    return added


def select(cells,retries,active):
    urgent=next((r for r in retries if r['state']=='pending' and
        any(r.get(f) for f in ('download_proxy_retry','hf_cache_lifecycle_repair_of','download_timeout_repair_of'))),None)
    return (urgent,True) if urgent else base.prior.old.select(cells,retries,active)


def main():
    validation=json.loads((AUDIT/'regression-result.json').read_text())
    assert validation['returncode']==0 and validation['no_model_calls']
    for item in validation['sources']:assert base.sha(base.PROJECT/item['path'])==item['sha256']
    base.NAME=NAME; base.ROOT=base.D/'results'/NAME
    base.PREVIOUS=base.D/'results/tb-dual-native-full-20261027-r40-download-timeout'
    base.AUDIT=AUDIT/'activation'
    base.OLD_UNIT='agenttx-tb-dual-full-20261027-r40-download-timeout.service'
    base.FROZEN=Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
    base.REPAIR_FILES=('experiments/scripts/bench_tb_image_native.py','experiments/scripts/run_tb_dual_local.py')
    original_register=base.register_shutdown_repairs
    def register(cells,retries):
        original_register(cells,retries)
        cache_repair.register_cache_repairs(cells,retries)
        added=register_timeout_repairs(cells,retries)
        if added:
            with (AUDIT/'registrations.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=base.now(),attempts=added))+'\n')
    original_save=base.save
    explicit_proxy_counts={}
    def save(path,data):
        nonlocal explicit_proxy_counts
        path=Path(path)
        if path==base.ROOT/'registration.json':
            data=dict(data,coordinator_script=str(Path(__file__)),coordinator_script_sha256=base.sha(__file__),
                runtime_repair=CONTRACT,download_timeout_contract=CONTRACT,uv_http_timeout_s=180,
                download_timeout_validation=str(AUDIT/'regression-result.json'),download_proxy_default_enabled=True,
                original_frozen_worker_sources_unchanged=False,native_task_containers_restarted=False,
                other_workers_restarted=False,failed_timeout_costs_retained=True,mixed_timeout_histories_excluded=True,
                user_instruction='八路GitHub/HF代理，其他DIRECT；修复已证40秒依赖超时，原worker不打断，8080不动')
        if path==base.ROOT/'progress.json':
            rows=data['tasks']+data['retry_tasks']
            explicit_proxy_counts=dict(collections.Counter(r['state'] for r in rows if r.get('download_proxy_retry')))
            data['download_retry']=dict(marked=sum(explicit_proxy_counts.values()),running=explicit_proxy_counts.get('running',0),network_skip=False)
            data['download_transport_default']=dict(enabled=True,domains='GitHub/HuggingFace',other_domains='DIRECT',port_8080='DIRECT',adopted_workers_keep_original_environment=True)
        if path==base.ROOT/'retry-report.json':
            data['download_proxy_default_enabled']=True
            data['download_retry_counts']=explicit_proxy_counts
        original_save(path,data)
    base.register_shutdown_repairs=register; base.select=select; base.save=save
    base.requires_proxy=lambda row:True
    base.main()


if __name__=='__main__':main()
