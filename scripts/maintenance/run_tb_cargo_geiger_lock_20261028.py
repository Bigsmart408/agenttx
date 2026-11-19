"""Adopt live workers and pin geiger prerequisites only for future port tasks."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_recovered_connection_20261027 as base
import run_tb_hf_cache_lifecycle_20261027 as cache_repair
import run_tb_verifier_download_timeout_20261027 as timeout_repair
import run_tb_reference_input_20261028 as reference_repair
import tb_verifier_index_fallback_20261028 as index_repair
from experiments.scripts.tb_verifier_cargo_lock import CONTRACT

NAME = 'tb-dual-native-full-20261028-r48-cargo-geiger-lock'
AUDIT = base.D / 'validation/tb-cargo-geiger-lock-20261028'
FILES = ('experiments/scripts/bench_tb_image_native.py',
         'experiments/scripts/run_tb_dual_local.py',
         'experiments/scripts/tb_verifier_cargo_lock.py')


def main():
    proof = json.loads((AUDIT / 'regression-result.json').read_text())
    assert proof['returncode'] == 0 and proof['no_model_calls']
    for row in proof['sources']:
        assert base.sha(base.PROJECT / row['path']) == row['sha256']
    for path in (proof['cold_install'], proof['native_probe']):
        check = json.loads(Path(path).read_text())
        assert check['returncode'] == 0 and check['no_model_calls']
        assert not check['running_workers_modified'] and not check['official_tests_executed']
    base.NAME = NAME
    base.ROOT = base.D / 'results' / NAME
    base.PREVIOUS = base.D / 'results/tb-dual-native-full-20261028-r47-huawei-verifier-fallback'
    base.AUDIT = AUDIT / 'activation'
    base.OLD_UNIT = 'agenttx-tb-dual-full-20261028-r47-huawei-verifier-fallback.service'
    base.FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions') / NAME / 'project'
    base.REPAIR_FILES = FILES
    original_register, original_annotate, original_save = base.register_shutdown_repairs, base.annotate, base.save

    def register(cells, retries):
        original_register(cells, retries)
        cache_repair.register_cache_repairs(cells, retries)
        timeout_repair.register_timeout_repairs(cells, retries)
        reference_repair.register_repairs(cells, retries)
        added = index_repair.register(cells, retries, NAME, base.key)
        if added:
            with (AUDIT / 'index-registrations.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=base.now(), attempts=added)) + '\n')

    def annotate(rows, registry):
        original_annotate(rows, registry)
        index_repair.annotate(rows)

    def select(cells, retries, active):
        urgent = next((r for r in retries if r['state'] == 'pending' and r.get('verifier_index_repair_of')), None)
        return (urgent, True) if urgent else reference_repair.repair.select(cells, retries, active)

    def save(path, data):
        path = Path(path)
        if path == base.ROOT / 'registration.json':
            data = dict(data, coordinator_script=str(Path(__file__)),
                        coordinator_script_sha256=base.sha(__file__), runtime_repair=CONTRACT,
                        verifier_toolchain_validation=str(AUDIT / 'regression-result.json'),
                        verifier_toolchain_task='port-compressor', download_proxy_default_enabled=True,
                        original_frozen_worker_sources_unchanged=False,
                        native_task_containers_restarted=False, other_workers_restarted=False,
                        failed_prerequisite_costs_retained=True, mixed_toolchain_histories_excluded=True,
                        outcome_based_retries_added=False,
                        user_instruction='继续八路并修复已证环境问题；GitHub/HF默认代理，其他DIRECT，8080不动')
        if path == base.ROOT / 'progress.json':
            data['download_transport_default'] = dict(enabled=True, domains='GitHub/HuggingFace',
                other_domains='DIRECT', port_8080='DIRECT', adopted_workers_keep_original_environment=True)
        if path == base.ROOT / 'retry-report.json':
            data['download_proxy_default_enabled'] = True
            data['verifier_toolchain_repair'] = dict(contract=CONTRACT, task='port-compressor',
                validation=str(AUDIT / 'regression-result.json'), mixed_histories_excluded=True)
        original_save(path, data)

    base.register_shutdown_repairs, base.annotate, base.select, base.save = register, annotate, select, save
    base.requires_proxy = lambda row: True
    base.main()


if __name__ == '__main__':
    main()
