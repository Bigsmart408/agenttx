import json
import subprocess

from experiments.scripts.bench_tb_image_native import (
    REFERENCE_INPUT_FILES, WASM_REFERENCE_INPUT_CONTRACT, verifier_entrypoint_script,
)
from experiments.scripts.run_tb_dual_local import configuration_id, result_configuration


def test_repeated_verifier_restores_both_inputs_and_preserves_solution(tmp_path):
    originals = tmp_path/'immutable'
    originals.mkdir()
    candidate = tmp_path/'run_pipeline.sh'
    candidate.write_text('candidate implementation must remain intact')
    pairs=[]
    for name in REFERENCE_INPUT_FILES['wasm-pipeline']:
        source=originals/name
        source.write_bytes(('pristine '+name).encode())
        target=tmp_path/name
        target.write_bytes(b'last randomized test input')
        pairs.append((source,target))
    test=tmp_path/'official-like.sh'
    body=['import pathlib']
    for source,target in pairs:
        body.append('assert pathlib.Path('+repr(str(target))+').read_bytes() == '+repr(source.read_bytes()))
        body.append('pathlib.Path('+repr(str(target))+').write_bytes(b"last randomized test input")')
    test.write_text("python3 - <<'CHECK'\n"+'\n'.join(body)+'\nCHECK\n')
    wrapper=tmp_path/'verifier.sh'
    wrapper.write_text(verifier_entrypoint_script(False,tmp_path/'results',test,reference_inputs=pairs))
    for _ in range(2):
        result=subprocess.run(['bash',str(wrapper)],capture_output=True)
        assert result.returncode==0,result.stderr
        assert candidate.read_text()=='candidate implementation must remain intact'
        assert all(target.read_bytes()==b'last randomized test input' for _,target in pairs)


def test_wasm_reset_is_task_scoped_and_separates_configuration(tmp_path):
    assert REFERENCE_INPUT_FILES['wasm-pipeline']==('module.wasm','libcompute.so')
    assert 'run_pipeline.sh' not in REFERENCE_INPUT_FILES['wasm-pipeline']
    path=tmp_path/'host-result.json'
    before=result_configuration({},path)
    after=result_configuration(dict(reference_input_contract=WASM_REFERENCE_INPUT_CONTRACT),path)
    assert configuration_id(before)!=configuration_id(after)


def test_residue_classifier_and_finite_retry_preserve_original_results(tmp_path, monkeypatch):
    import copy
    from scripts.maintenance import run_tb_wasm_reference_input_20261030 as repair
    audit=tmp_path/'audit';audit.mkdir();(audit/'validation.json').write_text('{}')
    monkeypatch.setattr(repair,'AUDIT',audit)
    path=tmp_path/'host-result.json'
    result=dict(official_success=False,model_returncode=0,model_finished=True,
                api_usage=dict(complete=True,total_tokens=12345))
    path.write_text(json.dumps(result))
    log=tmp_path/'verifier.stdout.log'
    log.write_text('FAILED ../tests/test_outputs.py::test_pipeline_runs_with_sample_files\n'
                   'PASSED ../tests/test_outputs.py::test_pipeline_runs_with_random_files_and_produces_expected_output\n'
                   'E         + -108866406211860134\n')
    row=dict(task='wasm-pipeline',harness='codex',policy='causal',state='completed',
             expected_context='compact',result_path=str(path))
    before=copy.deepcopy(row);raw=path.read_bytes();retries=[]
    assert repair.evidence(row)
    repair.register([row],retries)
    repair.register([row],retries)
    assert len(retries)==1 and retries[0]['wasm_input_repair_cohort']==repair.NAME
    assert row==before and path.read_bytes()==raw
    assert repair.evidence(dict(row,task='unrelated')) is None
    result['reference_input_contract']=repair.CONTRACT;path.write_text(json.dumps(result))
    assert repair.evidence(row) is None
    result.pop('reference_input_contract');path.write_text(json.dumps(result))
    log.write_text('FAILED unrelated assertion')
    assert repair.evidence(row) is None
