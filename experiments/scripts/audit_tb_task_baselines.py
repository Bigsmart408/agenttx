#!/usr/bin/env python3
"""Audit specific unsolved-task failures without supplying task implementations."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import shutil
import subprocess
from experiments.scripts.validate_tb_ready_local import DATA,digest,save,now
from experiments.scripts.run_tb_dual_local import ROOT,cell_command
from experiments.workloads import terminal_bench_suite as tb

# These errors name outputs explicitly required in the original instructions.
# This is deliberately not a general allowance for collection/import errors.
SPECS={
 'grid-pattern-transform':('tb-bootstrap-repair-20261019-r1','grid_transform.py',"No module named 'grid_transform'"),
 'pcap-to-netflow':('tb-bootstrap-repair-20261019-r1','/app/pcap_to_netflow.py','Failed to spawn: `/app/pcap_to_netflow.py`'),
 'solana-data':('tb-bootstrap-repair-20261019-r1','Run the server on port 5000','Solana service is not responding'),
 'magsac-install':('tb-bootstrap-repair-20261019-r1','remember to have numpy ready',"No module named 'numpy'"),
 'implement-eigenvectors-from-eigenvalues-research-paper':('tb-new-images-bootstrap-20261020-r1','abs_eigvec_from_spectra',"cannot import name 'abs_eigvec_from_spectra'"),
 'broken-python':('tb-basic-official-validation-20261019-r1',"can't seem to install packages with pip", "No module named 'pip'"),
 'broken-networking':('tb-basic-official-validation-20261019-r1','networking on this machine is broken','dns error: Device or resource busy'),
 'add-benchmark-lm-eval-harness':('tb-bootstrap-repair-20261019-r1','install the python library system-wide',"No module named 'pandas'"),
 'build-tcc-qemu':('tb-bootstrap-repair-20261019-r1','-cdrom /tmp/tcc.iso','IndexError: list index out of range'),
 'accelerate-maximal-square':('tb-accelerate-bootstrap-20261020-r1','/app/maximal_square.py',
     "Error importing maximal_square module: [Errno 2] No such file or directory: '/app'"),
 'install-windows-3.11':('tb-win311-bootstrap-20261020-r2',
     'Run Windows 3.11 for Workgroups in a virtual machine using qemu',
     'QEMU process not found - Windows 3.11 VM is not running'),
 'leelachess0-pytorch-conversion':('tb-lc0-bootstrap-20261020-r7',
     'Create a file called lc0utils.py in /app/ that contains the LC0Net class.',
     "No module named 'lc0utils'"),
}
NO_REPORT_TASKS={'broken-python','broken-networking'}


def validate_audit(row,source):
    audit=row.get('task_baseline_audit')
    assert audit and row['task'] in SPECS,'unsupported task baseline'
    _,instruction,error=SPECS[row['task']]
    assert audit['instruction_anchor']==instruction and audit['expected_error']==error
    assert instruction in (source/'task.yaml').read_text()
    assert audit['task_yaml_sha256']==digest(source/'task.yaml')
    assert row['original_script_sha256']==digest(source/'run-tests.sh')
    prior=Path(audit['original_result']);assert prior.resolve().is_relative_to(DATA/'validation')
    assert digest(prior)==audit['original_result_sha256']
    for name,expected in audit['diagnostic_sha256'].items():
        assert name in {'baseline.stdout','baseline.stderr','baseline.xml'}
        assert digest(prior.parent/name)==expected
    log='baseline.stderr' if row['task'] in NO_REPORT_TASKS else 'baseline.stdout'
    assert error in (prior.parent/log).read_text()
    tests={str(p.relative_to(source/'tests')):digest(p) for p in (source/'tests').rglob('*') if p.is_file()}
    assert row['test_assets']==tests
    if row['task']=='leelachess0-pytorch-conversion':
        import xml.etree.ElementTree as ET
        from experiments.scripts.repair_tb_lc0_verifier_source import COMMIT,TREE
        from experiments.scripts.validate_tb_bootstrap_batch import effective_script
        proof_path=prior.parent/'lc0-verifier-prerequisites.json'
        assert digest(proof_path)==row['verifier_prerequisites_sha256']
        proof=json.loads(proof_path.read_text())
        assert proof==row['verifier_prerequisites']
        assert proof['official_reference_loads'] and proof['model_calls']==0
        assert proof['source_commit']==COMMIT and proof['gold_onnx_bytes']>1024
        assert len(proof['gold_onnx_sha256'])==64
        assert {k:proof[k] for k in ['numpy','torch','torchvision','onnx','onnxruntime']}==dict(
            numpy='2.2.0',torch='2.8.0+cpu',torchvision='0.23.0+cpu',onnx='1.18.0',onnxruntime='1.22.1')
        assert row['verifier_tool_source']['commit']==COMMIT and row['verifier_tool_source']['tree']==TREE
        assert row['cpu_wheel_assets']==tb._LC0_CPU_WHEELS
        expected,_=effective_script((source/'run-tests.sh').read_text(),row['task'],
            lc0_wheels=True,lc0_source=True,lc0_packages=True)
        assert (prior.parent/'effective-run-tests.sh').read_text()==expected
        assert row['effective_script_sha256']==digest(prior.parent/'effective-run-tests.sh')
        report=ET.parse(prior.parent/'baseline.xml').getroot()
        errors=list(report.iter('error'))
        assert len(errors)==1 and errors[0].get('message')=='collection failure'
        assert ''.join(errors[0].itertext()).strip().endswith("ModuleNotFoundError: "+error)
        assert not list(report.iter('failure'))
    if row['task']=='install-windows-3.11':
        import xml.etree.ElementTree as ET
        report=ET.parse(prior.parent/'baseline.xml').getroot()
        errors=list(report.iter('error'))
        assert errors and all(error in item.get('message','') for item in errors)
        expected_failures={
            'test_network_status':'AssertionError: VNC service not listening on port 5901',
            'test_windows_keys_with_visual_feedback':'AssertionError: Windows keyboard test failed: Failed to take baseline screenshot'}
        found={}
        for case in report.iter('testcase'):
            for item in case.iter('failure'):
                found[case.get('name')]=item.get('message','')
        assert set(found)==set(expected_failures)
        assert all(found[name].startswith(message) for name,message in expected_failures.items())
    if row['task']=='accelerate-maximal-square':
        from experiments.scripts.validate_tb_bootstrap_batch import effective_script
        expected,_=effective_script((source/'run-tests.sh').read_text(),row['task'])
        assert (prior.parent/'effective-run-tests.sh').read_text()==expected
        assert row['effective_script_sha256']==digest(prior.parent/'effective-run-tests.sh')
        exception=row['verifier_compatibility_exception']
        assert exception['original_pytest']=='8.4.1' and exception['effective_pytest']=='6.2.5'
        assert exception['task_interpreter_unmodified'] and exception['test_assertions_unmodified']
        assert not exception['original_verifier_pin_preserved']
    harnesses=set()
    for name,expected in audit['native_preflight_sha256'].items():
        p=Path(name);assert p.resolve().is_relative_to(DATA/'validation')
        assert digest(p)==expected
        check=json.loads(p.read_text())
        assert check['completed'] and check['status']=='preflight_passed' and check['model_calls']==0
        assert check['task']==row['task'] and check['agent_runtime']=='official_task_container'
        assert check['image_id']==row['image_id'],'native preflight used a different task image'
        if row['task']=='accelerate-maximal-square':
            assert check.get('adapter_import_preflight'),'native adapter imports were not verified'
        harnesses.add(check['harness'])
    assert len(audit['native_preflight_sha256'])==2 and harnesses=={'sweagent','codex'}
    assert row['model_calls']==0 and not row['gold_solution_used']
    if row['task']=='build-tcc-qemu':
        path=Path(audit['boot_evidence']);assert path.resolve().is_relative_to(DATA/'validation')
        assert digest(path)==audit['boot_evidence_sha256']
        boot=json.loads(path.read_text())
        assert boot['kernel_boots_to_login'] and boot['image_id']==row['image_id']
        assert boot['missing_task_output']=='/tmp/tcc.iso' and boot['model_calls']==0
        assert digest(path.parent/'boot.log')==boot['boot_log_sha256']
        assert 'localhost login:' in (path.parent/'boot.log').read_text()
    return True


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--tasks',nargs='+',choices=sorted(SPECS))
    parser.add_argument('--native-preflights-from',type=Path)
    args=parser.parse_args()
    if args.native_preflights_from:
        assert args.native_preflights_from.resolve().is_relative_to(DATA/'validation')
    selected=args.tasks or list(SPECS)
    assert args.output.resolve().is_relative_to(DATA/'validation');args.output.mkdir()
    tasks=tb.load_tasks(DATA/'cache')
    def check(pair):
        task,harness=pair
        if args.native_preflights_from:
            path=args.native_preflights_from/task/harness/'causal/host-result.json'
            result=json.loads(path.read_text())
            assert result['completed'] and result['status']=='preflight_passed' and result['model_calls']==0
            assert result['task']==task and result['harness']==harness
            return task,path
        folder=args.output/task/harness
        folder.parent.mkdir(exist_ok=True)
        command=cell_command(ROOT,folder,dict(task=task,harness=harness,policy='causal'),None)+['--preflight-only']
        with folder.with_suffix('.log').open('w') as stream:
            subprocess.run(command,check=True,stdout=stream,stderr=subprocess.STDOUT,timeout=400)
        path=folder/'causal/host-result.json'
        result=json.loads(path.read_text())
        assert result['completed'] and result['status']=='preflight_passed' and result['model_calls']==0
        return task,path
    with ThreadPoolExecutor(max_workers=2) as pool:
        proofs=list(pool.map(check,[(t,h) for t in selected for h in ['sweagent','codex']]))
    rows=[]
    for task in selected:
        attempt,instruction,error=SPECS[task]
        source=tb.task_source(DATA/'cache',tasks[task]);prior=DATA/'validation'/attempt/task/'result.json'
        row=json.loads(prior.read_text());folder=args.output/task
        folder.mkdir(exist_ok=True)
        expected='no_test_report' if task in NO_REPORT_TASKS else 'test_setup_or_collection_error'
        assert not row['official_tests_executed'] and row['baseline_classification']==expected
        for name in ['run.sh','command.json','effective-run-tests.sh']:
            shutil.copy2(prior.parent/name,folder/name)
        row['task_baseline_audit']=dict(audited_at=now(),instruction_anchor=instruction,expected_error=error,
            task_yaml_sha256=digest(source/'task.yaml'),original_result=str(prior),original_result_sha256=digest(prior),
            diagnostic_sha256={n:digest(prior.parent/n) for n in ['baseline.stdout','baseline.stderr','baseline.xml'] if (prior.parent/n).exists()},
            native_preflight_sha256={str(p):digest(p) for t,p in proofs if t==task},
            verdict='Expected unsolved task output is absent. Original test assertions and starting image remain unchanged; any verifier compatibility exception is recorded separately.')
        if task=='build-tcc-qemu':
            boot=DATA/'validation/tb-tcc-boot-proof-20261020-r1/result.json'
            row['task_baseline_audit'].update(boot_evidence=str(boot),boot_evidence_sha256=digest(boot))
        validate_audit(row,source);save(folder/'result.json',row);rows.append(row)
    save(args.output/'execution.json',dict(finished_at=now(),tasks=rows,model_calls=0,
        official_tests_executed=False,task_baselines_audited=len(rows),task_solutions_applied=False))


if __name__=='__main__':main()
