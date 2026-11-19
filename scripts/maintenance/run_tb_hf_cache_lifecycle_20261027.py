#!/usr/bin/env python3
"""Continue eight domain-proxied slots with task-owned HF cache lifetime."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_recovered_connection_20261027 as base

NAME = 'tb-dual-native-full-20261027-r39-task-hf-cache'
AUDIT = base.D/'validation/tb-hf-cache-lifecycle-repair-20261027'
CONTRACT = 'task-hf-home-survives-adapter-exit/v1'


def cache_fault(row):
    if row['task']!='oom' or row['harness']!='codex' or row['state'] not in base.TERMINAL:
        return None
    path=Path(row['result_path']); result=json.loads(path.read_text())
    if result.get('hf_cache_contract') or result.get('success') or result.get('official_tests_rc')!=1:
        return None
    stdout=path.parent/'verifier.stdout.log'
    if not stdout.exists():return None
    text=stdout.read_text(errors='replace')
    required=("cache_dir = '/root/.cache/huggingface/hub'", 'local_files_only = True',
              'huggingface_hub.errors.LocalEntryNotFoundError: Cannot find the requested files',
              'FAILED ../tests/test_outputs.py::test_offline_model_load')
    if not all(s in text for s in required):return None
    diagnosis=json.loads((AUDIT/'diagnosis.json').read_text())
    for item in diagnosis['evidence']:
        assert base.sha(item['path'])==item['sha256']
    return dict(classification='task_offline_hf_cache_deleted_by_adapter_exit',
                result_path=str(path),result_sha256=base.sha(path),
                verifier_stdout_path=str(stdout),verifier_stdout_sha256=base.sha(stdout),
                diagnosis_path=str(AUDIT/'diagnosis.json'),diagnosis_sha256=base.sha(AUDIT/'diagnosis.json'))


def register_cache_repairs(cells,retries):
    latest={base.key(r):r for r in cells}
    for row in retries:
        if row['state'] in base.TERMINAL:latest[base.key(row)]=row
    added=[]
    for k,old in latest.items():
        history=[r for r in retries if base.key(r)==k]
        if any(r['state']=='running' or r.get('hf_cache_lifecycle_repair_of') for r in history):continue
        proof=cache_fault(old)
        if proof is None:continue
        first=next(r for r in cells if base.key(r)==k)
        pending=next((r for r in history if r['state']=='pending'),None)
        if pending is None:
            pending=dict(task=k[0],harness=k[1],policy=k[2],expected_context=first['expected_context'],
                state='pending',attempt=max([0]+[r.get('attempt',1) for r in history])+1,
                original_state=first['state'],original_result_path=first['result_path'],
                original_result_sha256=base.sha(first['result_path']))
            retries.append(pending)
        pending.update(hf_cache_lifecycle_repair_of=old['result_path'],
            hf_cache_lifecycle_repair_cohort=NAME,hf_cache_failure_evidence=proof,
            previous_attempt_path=old['result_path'],previous_attempt_sha256=base.sha(old['result_path']),
            retry_reason='one_evidenced_task_hf_cache_lifecycle_environment_repair')
        added.append(pending)
    return added


def select(cells,retries,active):
    urgent=next((r for r in retries if r['state']=='pending' and
                 (r.get('download_proxy_retry') or r.get('hf_cache_lifecycle_repair_of'))),None)
    if urgent:return urgent,True
    return base.prior.old.select(cells,retries,active)


def main():
    validation=json.loads((AUDIT/'regression-result.json').read_text())
    assert validation['returncode']==0 and validation['no_model_calls']
    base.NAME=NAME; base.ROOT=base.D/'results'/NAME
    base.PREVIOUS=base.D/'results/tb-dual-native-full-20261027-r38-default-domain-proxy'
    base.AUDIT=AUDIT/'activation'
    base.OLD_UNIT='agenttx-tb-dual-full-20261027-r38-default-domain-proxy.service'
    base.FROZEN=Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
    base.REPAIR_FILES=('experiments/scripts/bench_tb_image_native.py',
                       'experiments/scripts/run_tb_dual_local.py')
    for item in validation['sources']:
        assert base.sha(base.PROJECT/item['path'])==item['sha256']
    original_register=base.register_shutdown_repairs
    def register(cells,retries):
        original_register(cells,retries)
        added=register_cache_repairs(cells,retries)
        if added:
            with (AUDIT/'registrations.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=base.now(),attempts=added))+'\n')
    original_save=base.save
    def save(path,data):
        if Path(path)==base.ROOT/'registration.json':
            data=dict(data,coordinator_script=str(Path(__file__)),
                coordinator_script_sha256=base.sha(__file__),runtime_repair=CONTRACT,
                hf_cache_contract=CONTRACT,hf_cache_validation=str(AUDIT/'regression-result.json'),
                download_proxy_default_enabled=True,
                original_frozen_worker_sources_unchanged=False,
                native_task_containers_restarted=False,other_workers_restarted=False,
                user_instruction='八路GitHub/Hugging Face走代理，其他DIRECT；修复已证HF缓存生命周期；8080不动',
                download_retry_transport='Every new slot: private-netns Mihomo; GitHub/HF only; other domains and 8080 DIRECT',
                failed_cache_costs_retained=True,mixed_cache_contract_histories_excluded=True)
        if Path(path)==base.ROOT/'progress.json':
            data['download_transport_default']=dict(enabled=True,domains='GitHub/HuggingFace',
                other_domains='DIRECT',port_8080='DIRECT',adopted_workers_keep_original_environment=True)
        if Path(path)==base.ROOT/'retry-report.json':
            data['download_proxy_default_enabled']=True
        original_save(path,data)
    base.register_shutdown_repairs=register
    base.requires_proxy=lambda row:True
    base.select=select; base.save=save
    base.main()


if __name__=='__main__':main()
