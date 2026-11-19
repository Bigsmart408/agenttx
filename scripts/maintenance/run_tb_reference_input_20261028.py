"""Resume reviewed native fork setup failures and repeatable verifier inputs."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))
import run_tb_common_root_20261028 as repair

base = repair.base
NAME = 'tb-dual-native-full-20261028-r43-reference-input-forks'
AUDIT = base.D/'validation/tb-native-verifier-input-20261028'
CONTRACT = 'shared-app-tmp-overlay-native-rename/v2-fork-compatible'
INPUT_CONTRACT = 'restore-pinned-task-inputs-before-each-verifier/v1'
PREVIOUS = 'tb-dual-native-full-20261028-r42-common-app-tmp'


def environment_fault(row):
    if row['state'] not in base.TERMINAL or row['task'] != 'path-tracing-reverse':
        return None
    path = Path(row['result_path'])
    result = json.loads(path.read_text())
    if result.get('success'):
        return None
    issue = row.get('issue') or result.get('error') or result.get('host_error') or ''
    classification = None
    if result.get('app_tmp_filesystem_contract') != CONTRACT and all(
            text in issue for text in ('agenttx-fork-', 'chroot_executable.sh', 'try worker startup failed')):
        classification = 'shared_root_hides_private_causal_fork_coordinator'
    log = path.parent/'verifier.stdout.log'
    if result.get('reference_input_contract') != INPUT_CONTRACT and log.is_file():
        text = log.read_text(errors='replace')
        if all(s in text for s in ('test_image_compiles', 'os.rename("mystery", "/tmp/mystery")',
                                  "FileNotFoundError: [Errno 2] No such file or directory: 'mystery' -> '/tmp/mystery'")):
            classification = 'official_verifier_consumed_original_task_input'
    if classification is None:
        return None
    return dict(classification=classification, result_path=str(path), result_sha256=base.sha(path),
                verifier_stdout_sha256=base.sha(log) if log.is_file() else None)


def register_repairs(cells, retries):
    latest = {base.key(r):r for r in cells}
    for row in retries:
        if row['state'] in base.TERMINAL:
            latest[base.key(row)] = row
    added = []
    for key, old in latest.items():
        history = [r for r in retries if base.key(r) == key]
        if any(r['state'] == 'running' or r.get('native_input_repair_cohort') == NAME for r in history):
            continue
        proof = environment_fault(old)
        if proof is None:
            continue
        original = next(r for r in cells if base.key(r) == key)
        pending = next((r for r in history if r['state'] == 'pending'), None)
        if pending is None:
            pending = dict(task=key[0],harness=key[1],policy=key[2],state='pending',
                           expected_context=original['expected_context'],
                           attempt=max([0]+[r.get('attempt',1) for r in history])+1,
                           original_state=original['state'],original_result_path=original['result_path'],
                           original_result_sha256=base.sha(original['result_path']))
            retries.append(pending)
        pending.update(native_input_repair_of=old['result_path'],native_input_repair_cohort=NAME,
                       native_input_failure_evidence=proof,previous_attempt_path=old['result_path'],
                       previous_attempt_sha256=base.sha(old['result_path']),
                       retry_reason='one_evidenced_private_fork_or_original_verifier_input_environment_repair')
        added.append(pending)
    return added


def review_inherited_halt(previous):
    path = base.D/'validation/tb-cross-filesystem-rename-20261028/activation/dispatch-halt.json'
    halt = json.loads(path.read_text())
    row = next(r for r in previous['tasks']+previous['retry_tasks']
               if r.get('result_path') == halt['row']['result_path'])
    proof = environment_fault(row)
    assert proof and proof['classification'] == 'shared_root_hides_private_causal_fork_coordinator'
    assert row['harness'] == 'sweagent' and row['policy'] == 'causal'
    assert proof['result_sha256'] == base.sha(halt['row']['result_path'])
    validation = json.loads((AUDIT/'regression-result.json').read_text())
    assert validation['returncode'] == 0 and validation['checks']['fork_parent_isolated']
    return dict(proof, reviewed_environment_repair=True, validation=str(AUDIT/'regression-result.json'),
                dispatch_halt_sha256=base.sha(path))


def main():
    repair.NAME = NAME
    repair.AUDIT = AUDIT
    repair.CONTRACT = CONTRACT
    repair.PREVIOUS_NAME = PREVIOUS
    repair.OLD_UNIT = 'agenttx-tb-dual-full-20261028-r42-common-app-tmp.service'
    repair.COORDINATOR_SCRIPT = Path(__file__)
    repair.EXTRA_REGISTER = register_repairs
    repair.REVIEW_INHERITED_HALT = review_inherited_halt
    repair.main()


if __name__ == '__main__':
    main()
