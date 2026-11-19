"""Resume dispatch after exact Flink timeout review; retain live workers and costs."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_reviewed_flink_whole_timeout_20261030 as previous
import run_tb_stream_reconnect_resume_20261029 as preflight

base = previous.base
NAME = 'tb-dual-native-full-20261030-r72-reviewed-flink-causal-timeout'
OLD = base.D / 'results' / previous.NAME
AUDIT = base.D / 'validation/tb-reviewed-flink-causal-timeout-20261030'


def review(progress):
    halt_path = previous.AUDIT / 'activation/dispatch-halt.json'
    halt = json.loads(halt_path.read_text())
    row = next(r for r in progress['tasks'] + progress['retry_tasks']
               if r.get('result_path') == halt['row']['result_path'])
    assert base.key(row) == ('vul-flink', 'codex', 'causal')
    path = Path(row['result_path'])
    result = json.loads(path.read_text())
    assert row['issue'] == 'incomplete_api_usage'
    assert result['model_returncode'] == 124 and result['model_timed_out']
    assert not result['api_usage']['complete']
    assert result['api_usage']['total_tokens'] is None
    assert halt['connection_evidence']['classification'] == 'explicit_model_connection_failure'
    stderr = path.parent / 'verifier.stderr.log'
    assert 'start-cluster.sh: No such file or directory' in stderr.read_text()
    probe_path = base.D / 'validation/tb-live-monitor-20261026/seven-api-tcp-check-20261030T0157.json'
    probe = json.loads(probe_path.read_text())
    assert len(probe['workers']) == 7
    assert all(r.get('probe', {}).get('api_tcp') for r in probe['workers'])
    assert probe['at'][:10] == base.now()[:10]
    proof = dict(at=base.now(), reviewed_environment_repair=True,
                 review_kind='exact_timeout_connection_notice_current_private_tcp_healthy',
                 historical_connection_root_cause='unresolved',
                 result_path=str(path), result_sha256=base.sha(path),
                 dispatch_halt_sha256=base.sha(halt_path),
                 verifier_stderr_sha256=base.sha(stderr),
                 private_tcp_probe=str(probe_path), private_tcp_probe_sha256=base.sha(probe_path),
                 usage=result['api_usage'], official_success=result['official_success'],
                 unknown_fees_imputed=False, prior_costs_retained=True,
                 affected_cell_excluded_from_complete_cost_comparison=True,
                 extra_outcome_based_retry_added=False, existing_workers_restarted=False,
                 model_budget_changed=False, host_8080_forwarding_modified=False)
    base.prior.save(AUDIT / 'reviewed-halt.json', proof)
    return proof


def fresh_network():
    preflight.OLD, preflight.AUDIT = OLD, AUDIT
    return preflight.fresh_network()


def main():
    AUDIT.mkdir(parents=True, exist_ok=True)
    original = base.main

    def deploy():
        base.NAME, base.ROOT = NAME, base.D / 'results' / NAME
        base.PREVIOUS = OLD
        base.OLD_UNIT = 'agenttx-tb-dual-full-20261030-r71-reviewed-flink-whole-timeout.service'
        base.AUDIT = AUDIT / 'activation'
        base.FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions') / NAME / 'project'
        base.REPAIR_FILES, base.REPAIR_SOURCE_OVERRIDES = (), {}
        base.REVIEW_INHERITED_HALT = review
        base.FRESH_NETWORK_PROOF = fresh_network
        old_save = base.save

        def save(path, data):
            if Path(path) == base.ROOT / 'registration.json':
                data = dict(data, coordinator_script=str(Path(__file__)),
                            coordinator_script_sha256=base.sha(__file__),
                            runtime_repair=previous.previous.CONTRACT,
                            coordinator_review='exact-flink-causal-timeout-no-runtime-change',
                            reviewed_missing_usage=str(AUDIT / 'reviewed-halt.json'),
                            unknown_fees_imputed=False, existing_workers_restarted=False)
                return base.prior.save(path, data)
            return old_save(path, data)

        base.save = save
        original()

    base.main = deploy
    previous.main()


if __name__ == '__main__':
    main()
