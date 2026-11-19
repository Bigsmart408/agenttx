"""Adopt live workers; repair an evidenced native /app -> /tmp rename failure."""
import collections
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_recovered_connection_20261027 as base
import run_tb_hf_cache_lifecycle_20261027 as cache_repair
import run_tb_verifier_download_timeout_20261027 as download_repair

NAME = 'tb-dual-native-full-20261028-r42-common-app-tmp'
AUDIT = base.D/'validation/tb-cross-filesystem-rename-20261028'
CONTRACT = 'shared-app-tmp-overlay-native-rename/v1'
PREVIOUS_NAME = 'tb-dual-native-full-20261027-r41-verifier-download-timeout'
OLD_UNIT = 'agenttx-tb-dual-full-20261027-r41-verifier-download-timeout.service'
COORDINATOR_SCRIPT = Path(__file__)
EXTRA_REGISTER = None
REVIEW_INHERITED_HALT = None


def filesystem_fault(row):
    if row['state'] not in base.TERMINAL or row['task'] != 'path-tracing-reverse':
        return None
    path = Path(row['result_path'])
    result = json.loads(path.read_text())
    if result.get('success') or result.get('app_tmp_filesystem_contract') == CONTRACT:
        return None
    log = path.parent/'verifier.stdout.log'
    if not log.is_file():
        return None
    text = log.read_text(errors='replace')
    if not all(s in text for s in ('OSError: [Errno 18] Invalid cross-device link',
                                  "os.rename(\"mystery\", \"/tmp/mystery\")")):
        return None
    return dict(classification='runner_split_app_tmp_breaks_official_native_rename',
                result_path=str(path), result_sha256=base.sha(path),
                verifier_stdout_path=str(log), verifier_stdout_sha256=base.sha(log))


def register_filesystem_repairs(cells, retries):
    latest = {base.key(r): r for r in cells}
    for row in retries:
        if row['state'] in base.TERMINAL:
            latest[base.key(row)] = row
    added = []
    for key, old in latest.items():
        history = [r for r in retries if base.key(r) == key]
        if any(r['state'] == 'running' or r.get('app_tmp_repair_cohort') == NAME for r in history):
            continue
        proof = filesystem_fault(old)
        if proof is None:
            continue
        first = next(r for r in cells if base.key(r) == key)
        pending = next((r for r in history if r['state'] == 'pending'), None)
        if pending is None:
            pending = dict(task=key[0], harness=key[1], policy=key[2],
                           expected_context=first['expected_context'], state='pending',
                           attempt=max([0]+[r.get('attempt', 1) for r in history])+1,
                           original_state=first['state'], original_result_path=first['result_path'],
                           original_result_sha256=base.sha(first['result_path']))
            retries.append(pending)
        pending.update(app_tmp_repair_of=old['result_path'], app_tmp_repair_cohort=NAME,
                       app_tmp_failure_evidence=proof, previous_attempt_path=old['result_path'],
                       previous_attempt_sha256=base.sha(old['result_path']),
                       retry_reason='one_evidenced_app_tmp_native_rename_environment_repair')
        added.append(pending)
    return added


def select(cells, retries, active):
    urgent = next((r for r in retries if r['state'] == 'pending' and
                   (r.get('app_tmp_repair_of') or r.get('native_input_repair_of'))), None)
    return (urgent, True) if urgent else download_repair.select(cells, retries, active)


def main():
    validation = json.loads((AUDIT/'regression-result.json').read_text())
    assert validation['returncode'] == 0 and validation['no_model_calls']
    for item in validation['sources']:
        assert base.sha(base.PROJECT/item['path']) == item['sha256']
    base.NAME = NAME
    base.ROOT = base.D/'results'/NAME
    base.PREVIOUS = base.D/'results'/PREVIOUS_NAME
    base.AUDIT = AUDIT/'activation'
    base.OLD_UNIT = OLD_UNIT
    base.REVIEW_INHERITED_HALT = REVIEW_INHERITED_HALT
    base.FROZEN = Path('/home/bfq/agenttx-workspace/agenttx-executions')/NAME/'project'
    base.REPAIR_FILES = ('experiments/scripts/bench_tb_image_native.py',
                         'experiments/scripts/run_tb_dual_local.py',
                         'scripts/patch_try_common_root.py')
    original_register = base.register_shutdown_repairs

    def register(cells, retries):
        original_register(cells, retries)
        cache_repair.register_cache_repairs(cells, retries)
        download_repair.register_timeout_repairs(cells, retries)
        added = register_filesystem_repairs(cells, retries)
        if EXTRA_REGISTER is not None:
            added += EXTRA_REGISTER(cells, retries)
        if added:
            with (AUDIT/'registrations.jsonl').open('a') as out:
                out.write(json.dumps(dict(at=base.now(), attempts=added))+'\n')

    original_save = base.save
    proxy_counts = {}

    def save(path, data):
        nonlocal proxy_counts
        path = Path(path)
        if path == base.ROOT/'registration.json':
            data = dict(data, coordinator_script=str(COORDINATOR_SCRIPT),
                        coordinator_script_sha256=base.sha(COORDINATOR_SCRIPT), runtime_repair=CONTRACT,
                        coordinator_base_script_sha256=base.sha(base.__file__),
                        coordinator_adapter_script_sha256=base.sha(__file__),
                        filesystem_repair_validation=str(AUDIT/'regression-result.json'),
                        filesystem_repair_tasks=['path-tracing-reverse'],
                        original_frozen_worker_sources_unchanged=False,
                        native_task_containers_restarted=False, other_workers_restarted=False,
                        failed_filesystem_costs_retained=True, mixed_filesystem_histories_excluded=True,
                        download_proxy_default_enabled=True,
                        user_instruction='八路GitHub/HF代理，其余DIRECT；修复已证跨文件系统重命名故障，原worker不打断，8080不动')
        if path == base.ROOT/'progress.json':
            rows = data['tasks']+data['retry_tasks']
            proxy_counts = dict(collections.Counter(r['state'] for r in rows if r.get('download_proxy_retry')))
            data['download_retry'] = dict(marked=sum(proxy_counts.values()), running=proxy_counts.get('running', 0), network_skip=False)
            data['download_transport_default'] = dict(enabled=True, domains='GitHub/HuggingFace',
                other_domains='DIRECT', port_8080='DIRECT', adopted_workers_keep_original_environment=True)
        if path == base.ROOT/'retry-report.json':
            data['download_proxy_default_enabled'] = True
            data['download_retry_counts'] = proxy_counts
        original_save(path, data)

    base.register_shutdown_repairs = register
    base.select = select
    base.save = save
    base.requires_proxy = lambda row: True
    base.main()


if __name__ == '__main__':
    main()
