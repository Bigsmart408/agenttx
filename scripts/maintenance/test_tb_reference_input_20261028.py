from pathlib import Path
import copy
import json
import subprocess
import sys

sys.path.insert(0,str(Path(__file__).parent))
import run_tb_reference_input_20261028 as repair

from experiments.scripts.bench_tb_image_native import verifier_entrypoint_script
from experiments.scripts.run_tb_dual_local import result_configuration, configuration_id


def test_repeated_verifier_restores_original_input_and_preserves_candidate(tmp_path):
    original = tmp_path/'immutable'
    reference = tmp_path/'mystery'
    moved = tmp_path/'moved'
    candidate = tmp_path/'mystery.c'
    original.write_bytes(b'original binary')
    reference.write_bytes(original.read_bytes())
    candidate.write_text('candidate must not change')
    script = tmp_path/'official-rename-step.sh'
    script.write_text("#!/bin/bash\npython3 -c " + "\"import os;os.rename('"+str(reference)+"','"+str(moved)+"')\"\n")
    legacy = tmp_path/'legacy.sh'
    legacy.write_text(verifier_entrypoint_script(False, tmp_path/'old-results', script))
    first = subprocess.run(['bash',str(legacy)],capture_output=True)
    second = subprocess.run(['bash',str(legacy)],capture_output=True)
    assert first.returncode == 0 and second.returncode != 0
    fixed = tmp_path/'fixed.sh'
    fixed.write_text(verifier_entrypoint_script(False,tmp_path/'new-results',script,
                                               reference_inputs=[(original,reference)]))
    for _ in range(2):
        result = subprocess.run(['bash',str(fixed)],capture_output=True)
        assert result.returncode == 0, result.stderr
        assert moved.read_bytes() == original.read_bytes()
        assert candidate.read_text() == 'candidate must not change'


def test_missing_immutable_input_fails_before_invoking_test(tmp_path):
    script = tmp_path/'test.sh'
    sentinel = tmp_path/'test-started'
    script.write_text('touch '+str(sentinel)+'\n')
    wrapper = tmp_path/'wrapper.sh'
    wrapper.write_text(verifier_entrypoint_script(False,tmp_path/'results',script,
                                                 reference_inputs=[(tmp_path/'missing',tmp_path/'target')]))
    result = subprocess.run(['bash',str(wrapper)],capture_output=True)
    assert result.returncode != 0 and not sentinel.exists()


def test_input_reset_configuration_is_not_pooled_with_previous_environment(tmp_path):
    path = tmp_path/'cell'/'host-result.json'
    original = result_configuration({},path)
    repaired = result_configuration(dict(reference_input_contract='restore-pinned-task-inputs-before-each-verifier/v1'),path)
    assert configuration_id(original) != configuration_id(repaired)


def failed_row(tmp_path, contract=None):
    path=tmp_path/'host-result.json'
    path.write_text(json.dumps(dict(success=False,reference_input_contract=contract)))
    (tmp_path/'verifier.stdout.log').write_text('test_image_compiles\nos.rename("mystery", "/tmp/mystery")\n'
        "FileNotFoundError: [Errno 2] No such file or directory: 'mystery' -> '/tmp/mystery'")
    return dict(task='path-tracing-reverse',harness='codex',policy='causal',expected_context='compact',
                state='completed',result_path=str(path))


def test_original_input_fault_does_not_match_repaired_or_unrelated_failure(tmp_path):
    row=failed_row(tmp_path)
    assert repair.environment_fault(row)['classification']=='official_verifier_consumed_original_task_input'
    assert repair.environment_fault(dict(row,task='unrelated')) is None
    row=failed_row(tmp_path,repair.INPUT_CONTRACT)
    assert repair.environment_fault(row) is None
    (tmp_path/'verifier.stdout.log').write_text('FAILED image similarity assertion')
    assert repair.environment_fault(row) is None


def test_finite_reference_repair_keeps_every_existing_fee_and_live_worker(tmp_path):
    row=failed_row(tmp_path);before=copy.deepcopy(row);raw=Path(row['result_path']).read_bytes();retries=[]
    assert len(repair.register_repairs([row],retries))==1
    assert row==before and Path(row['result_path']).read_bytes()==raw
    assert repair.register_repairs([row],retries)==[]
    assert retries[0]['previous_attempt_path']==row['result_path']
    assert repair.register_repairs([row],[dict(row,state='running')])==[]


def test_private_fork_failure_requires_exact_evidence_and_new_contract_stops_retry(tmp_path):
    row=failed_row(tmp_path,repair.INPUT_CONTRACT)
    row['issue']='try worker startup failed: /tmp/agenttx-fork-abc/chroot_executable.sh missing'
    assert repair.environment_fault(row)['classification']=='shared_root_hides_private_causal_fork_coordinator'
    path=Path(row['result_path']);d=json.loads(path.read_text());d['app_tmp_filesystem_contract']=repair.CONTRACT
    path.write_text(json.dumps(d))
    assert repair.environment_fault(row) is None
