import json
from pathlib import Path
import pytest
from experiments.scripts import audit_tb_task_baselines as audit


def fixture(tmp_path,monkeypatch):
    monkeypatch.setattr(audit,'DATA',tmp_path)
    source=tmp_path/'source';source.mkdir();(source/'tests').mkdir()
    (source/'task.yaml').write_text('Create grid_transform.py')
    (source/'run-tests.sh').write_text('pytest /tests')
    folder=tmp_path/'validation/probe';folder.mkdir(parents=True)
    (folder/'result.json').write_text('{}')
    (folder/'baseline.stdout').write_text("No module named 'grid_transform'")
    (folder/'baseline.stderr').write_text('')
    (folder/'baseline.xml').write_text('<testsuites/>')
    preflights={}
    for harness in ['codex','sweagent']:
        p=folder/(harness+'.json')
        p.write_text(json.dumps(dict(completed=True,status='preflight_passed',model_calls=0,
            task='grid-pattern-transform',harness=harness,agent_runtime='official_task_container',image_id='sha256:task-image')))
        preflights[str(p)]=audit.digest(p)
    row=dict(task='grid-pattern-transform',model_calls=0,gold_solution_used=False,test_assets={},image_id='sha256:task-image',
        original_script_sha256=audit.digest(source/'run-tests.sh'),task_baseline_audit=dict(
        instruction_anchor='grid_transform.py',expected_error="No module named 'grid_transform'",
        task_yaml_sha256=audit.digest(source/'task.yaml'),original_result=str(folder/'result.json'),
        original_result_sha256=audit.digest(folder/'result.json'),
        diagnostic_sha256={n:audit.digest(folder/n) for n in ['baseline.stdout','baseline.stderr','baseline.xml']},
        native_preflight_sha256=preflights))
    return source,row


def test_task_required_missing_module_requires_original_instruction_and_diagnostics(tmp_path,monkeypatch):
    source,row=fixture(tmp_path,monkeypatch)
    assert audit.validate_audit(row,source)
    (source/'task.yaml').write_text('Different task')
    with pytest.raises(AssertionError):audit.validate_audit(row,source)


def test_unrelated_import_error_and_wrong_task_preflight_are_rejected(tmp_path,monkeypatch):
    source,row=fixture(tmp_path,monkeypatch)
    row['task_baseline_audit']['expected_error']="No module named 'pytest'"
    with pytest.raises(AssertionError):audit.validate_audit(row,source)
    row['task_baseline_audit']['expected_error']="No module named 'grid_transform'"
    key=next(iter(row['task_baseline_audit']['native_preflight_sha256']))
    p=Path(key);obj=json.loads(p.read_text());obj['task']='unrelated-task';p.write_text(json.dumps(obj))
    row['task_baseline_audit']['native_preflight_sha256'][key]=audit.digest(p)
    with pytest.raises(AssertionError):audit.validate_audit(row,source)


def lc0_fixture(tmp_path,monkeypatch):
    from experiments.scripts.repair_tb_lc0_verifier_source import COMMIT,TREE
    from experiments.scripts.validate_tb_bootstrap_batch import effective_script
    source,row=fixture(tmp_path,monkeypatch)
    task='leelachess0-pytorch-conversion'
    _,anchor,error=audit.SPECS[task]
    folder=Path(row['task_baseline_audit']['original_result']).parent
    (source/'task.yaml').write_text(anchor)
    original='git clone https://github.com/LeelaChessZero/lc0.git\nuv pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu\n'
    (source/'run-tests.sh').write_text(original)
    effective,_=effective_script(original,task,lc0_wheels=True,lc0_source=True,lc0_packages=True)
    (folder/'effective-run-tests.sh').write_text(effective)
    (folder/'baseline.stdout').write_text(error)
    (folder/'baseline.xml').write_text('<testsuites><testsuite><testcase><error message="collection failure">ModuleNotFoundError: '+error+'</error></testcase></testsuite></testsuites>')
    proof=dict(source_commit=COMMIT,gold_onnx_sha256='a'*64,gold_onnx_bytes=123456,
        official_reference_loads=True,model_calls=0,numpy='2.2.0',torch='2.8.0+cpu',
        torchvision='0.23.0+cpu',onnx='1.18.0',onnxruntime='1.22.1')
    p=folder/'lc0-verifier-prerequisites.json';p.write_text(json.dumps(proof))
    row.update(task=task,original_script_sha256=audit.digest(source/'run-tests.sh'),
        effective_script_sha256=audit.digest(folder/'effective-run-tests.sh'),
        verifier_prerequisites=proof,verifier_prerequisites_sha256=audit.digest(p),
        verifier_tool_source=dict(commit=COMMIT,tree=TREE),cpu_wheel_assets=audit.tb._LC0_CPU_WHEELS.copy())
    entry=row['task_baseline_audit']
    entry.update(instruction_anchor=anchor,expected_error=error,task_yaml_sha256=audit.digest(source/'task.yaml'),
        diagnostic_sha256={n:audit.digest(folder/n) for n in ['baseline.stdout','baseline.stderr','baseline.xml']})
    for name in entry['native_preflight_sha256']:
        q=Path(name);obj=json.loads(q.read_text());obj['task']=task;q.write_text(json.dumps(obj))
        entry['native_preflight_sha256'][name]=audit.digest(q)
    return source,row


@pytest.mark.parametrize('fault',['missing_reference','wrong_version','tampered_proof','extra_error'])
def test_lc0_missing_agent_module_cannot_mask_broken_verifier(tmp_path,monkeypatch,fault):
    source,row=lc0_fixture(tmp_path,monkeypatch)
    assert audit.validate_audit(row,source)
    folder=Path(row['task_baseline_audit']['original_result']).parent
    p=folder/'lc0-verifier-prerequisites.json'
    if fault=='missing_reference':row['verifier_prerequisites']['official_reference_loads']=False
    elif fault=='wrong_version':row['verifier_prerequisites']['torch']='2.8.0+cu128'
    elif fault=='tampered_proof':p.write_text('{}')
    else:
        q=folder/'baseline.xml'
        q.write_text(q.read_text().replace('</testcase>','<error message="compiler failed"/></testcase>'))
        row['task_baseline_audit']['diagnostic_sha256']['baseline.xml']=audit.digest(q)
    if fault in {'missing_reference','wrong_version'}:
        p.write_text(json.dumps(row['verifier_prerequisites']))
        row['verifier_prerequisites_sha256']=audit.digest(p)
    with pytest.raises(AssertionError):audit.validate_audit(row,source)
