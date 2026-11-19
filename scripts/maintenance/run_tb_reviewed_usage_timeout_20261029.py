"""Reviewed continuation: retain unknown timeout fees and adopt live workers unchanged."""
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).parent))
import run_tb_sudo_render_classification_20261029 as previous
import run_tb_stream_reconnect_resume_20261029 as preflight
base = previous.base
NAME = 'tb-dual-native-full-20261029-r66-reviewed-usage-timeout'
OLD = base.D/'results'/previous.NAME
AUDIT = base.D/'validation/tb-reviewed-usage-timeout-20261029'

def review(progress):
    rows=[]
    for row in progress['tasks']+progress['retry_tasks']:
        if row.get('execution_cohort') != previous.NAME or row.get('issue') != 'incomplete_api_usage':
            continue
        path=Path(row['result_path']); result=json.loads(path.read_text())
        if row['task']!='vul-flink' or row['harness']!='codex':
            continue
        assert result['model_returncode']==124 and result['model_timed_out']
        assert not result['api_usage']['complete'] and result['api_usage']['total_tokens'] is None
        rows.append(dict(result_path=str(path),sha256=base.sha(path),policy=row['policy'],
                         usage=result['api_usage'],official_success=result['official_success']))
    assert rows, 'Exact timeout evidence required'
    proof=dict(at=base.now(),reviewed_environment_repair=True,
               review_kind='reviewed_timeout_missing_final_usage_no_runtime_change',rows=rows,
               unknown_fees_retained=True,unknown_fees_imputed=False,
               affected_cells_excluded_from_complete_cost_comparison=True,
               extra_outcome_based_retry_added=False,existing_retry_policy_unchanged=True,
               model_budget_changed=False,host_8080_forwarding_modified=False)
    base.prior.save(AUDIT/'reviewed-halt.json',proof)
    return proof

def fresh_network():
    preflight.OLD=OLD; preflight.AUDIT=AUDIT
    return preflight.fresh_network()

def main():
    AUDIT.mkdir(parents=True,exist_ok=True)
    original=base.main
    def deploy():
        base.NAME,base.ROOT=NAME,base.D/'results'/NAME
        base.PREVIOUS=OLD
        base.OLD_UNIT='agenttx-tb-dual-full-20261029-r65-sudo-render-failure-classification.service'
        base.AUDIT=AUDIT/'activation'
        base.FROZEN=Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
        base.REPAIR_FILES=(); base.REPAIR_SOURCE_OVERRIDES={}
        base.REVIEW_INHERITED_HALT=review
        base.FRESH_NETWORK_PROOF=fresh_network
        old_save=base.save
        def save(path,data):
            if Path(path)==base.ROOT/'registration.json':
                data=dict(data,coordinator_script=str(Path(__file__)),
                          coordinator_script_sha256=base.sha(__file__),
                          runtime_repair=previous.CONTRACT,
                          coordinator_review='reviewed-timeout-usage-no-runtime-change',
                          reviewed_missing_usage=str(AUDIT/'reviewed-halt.json'),
                          unknown_fees_imputed=False,existing_workers_restarted=False)
                return base.prior.save(path,data)
            old_save(path,data)
        base.save=save
        original()
    base.main=deploy
    previous.main()
if __name__=='__main__': main()
