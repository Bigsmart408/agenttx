"""Adopt eight workers and repair only future verifier dependency transport."""
import collections
import json
from pathlib import Path
import sys

sys.path.insert(0,str(Path(__file__).parent))
import run_tb_recovered_connection_20261027 as base
import run_tb_hf_cache_lifecycle_20261027 as cache_repair
import run_tb_verifier_download_timeout_20261027 as timeout_repair
import run_tb_reference_input_20261028 as reference_repair
import tb_verifier_index_fallback_20261028 as index_repair

NAME='tb-dual-native-full-20261028-r45-reviewed-model-signal'
AUDIT=base.D/'validation/tb-reviewed-model-signal-20261028'
FILES=('experiments/scripts/bench_tb_image_native.py',
       'experiments/scripts/run_tb_dual_local.py',
       'experiments/scripts/tb_verifier_download_fallback.py')


def review_inherited_halt(previous):
    proof=json.loads((AUDIT/'reviewed-halt.json').read_text())
    row=next(r for r in previous['tasks']+previous['retry_tasks'] if r.get('result_path')==proof['result_path'])
    assert base.key(row)==('play-zork-easy','codex','whole_branch_abort')
    assert row['issue']=='incomplete_api_usage' and row['state']=='infrastructure_error'
    result=json.loads(Path(proof['result_path']).read_text())
    assert base.sha(proof['result_path'])==proof['result_sha256']
    assert result['model_returncode']==-15 and not result['api_usage']['complete']
    assert result['official_success'] is False and proof['final_tokens'] is None
    for path_key,hash_key in (('dispatch_halt','dispatch_halt_sha256'),('model_signal_audit','model_signal_audit_sha256'),('observed_usage_snapshot','observed_usage_snapshot_sha256')):
        assert base.sha(proof[path_key])==proof[hash_key]
    assert proof['recorded_tokens_lower_bound']==104886 and not proof['signal_source_confirmed']
    assert not proof['repair_applied'] and proof['no_model_calls']
    # Required by the inherited review hook; this is a reviewed exception,
    # not a claim that a task or algorithm repair has been applied.
    return dict(proof,reviewed_environment_repair=True,review_kind='single_terminal_exception_no_runtime_change')


def main():
    proof=json.loads((AUDIT/'regression-result.json').read_text())
    assert proof['returncode']==0 and proof['no_model_calls']
    for row in proof['sources']:assert base.sha(base.PROJECT/row['path'])==row['sha256']
    native=json.loads(Path(proof['native_probe']).read_text())
    assert native['returncode']==0 and native['no_model_calls'] and not native['running_workers_modified']
    base.NAME=NAME; base.ROOT=base.D/'results'/NAME
    base.PREVIOUS=base.D/'results/tb-dual-native-full-20261028-r44-verifier-index-fallback'
    base.AUDIT=AUDIT/'activation'
    base.OLD_UNIT='agenttx-tb-dual-full-20261028-r44-verifier-index-fallback.service'
    base.FROZEN=Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
    base.REPAIR_FILES=()
    base.REVIEW_INHERITED_HALT=review_inherited_halt
    original_register=base.register_shutdown_repairs
    original_annotate=base.annotate
    original_save=base.save

    def register(cells,retries):
        original_register(cells,retries)
        cache_repair.register_cache_repairs(cells,retries)
        timeout_repair.register_timeout_repairs(cells,retries)
        reference_repair.register_repairs(cells,retries)
        added=index_repair.register(cells,retries,NAME,base.key)
        if added:
            with (AUDIT/'registrations.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=base.now(),attempts=added))+'\n')

    def annotate(rows,registry):
        original_annotate(rows,registry)
        index_repair.annotate(rows)

    def select(cells,retries,active):
        urgent=next((r for r in retries if r['state']=='pending' and r.get('verifier_index_repair_of')),None)
        return (urgent,True) if urgent else reference_repair.repair.select(cells,retries,active)

    def save(path,data):
        path=Path(path)
        if path==base.ROOT/'registration.json':
            data=dict(data,coordinator_script=str(Path(__file__)),coordinator_script_sha256=base.sha(__file__),
                      runtime_repair=index_repair.CONTRACT,verifier_download_contract=index_repair.CONTRACT,
                      verifier_download_validation=str(AUDIT/'regression-result.json'),
                      download_proxy_default_enabled=True,original_frozen_worker_sources_unchanged=True,
                      reviewed_signal_exception=str(AUDIT/'reviewed-halt.json'),
                      runtime_or_algorithm_changed=False,
                      native_task_containers_restarted=False,other_workers_restarted=False,
                      failed_prerequisite_costs_retained=True,mixed_transport_histories_excluded=True,
                      user_instruction='持续监控并修复已证验收依赖下载失败；八路GitHub/HF代理、其他DIRECT、8080不动')
        if path==base.ROOT/'progress.json':
            data['download_transport_default']=dict(enabled=True,domains='GitHub/HuggingFace',
                other_domains='DIRECT',port_8080='DIRECT',adopted_workers_keep_original_environment=True)
        if path==base.ROOT/'retry-report.json':data['download_proxy_default_enabled']=True
        original_save(path,data)

    base.register_shutdown_repairs=register
    base.annotate=annotate
    base.select=select
    base.save=save
    base.requires_proxy=lambda row:True
    base.main()


if __name__=='__main__':main()
