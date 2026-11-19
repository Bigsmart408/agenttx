import json

from scripts.maintenance import tb_hf_prerequisite_evidence_20261026 as evidence


def test_requires_real_official_installer_stall_not_only_timeout(tmp_path):
    path=tmp_path/'host-result.json';result=dict(status='verifier_timeout',official_tests_rc=124)
    path.write_text(json.dumps(result));(tmp_path/'verifier.stdout.log').write_text('')
    row=dict(task='hf-train-lora-adapter',harness='codex',policy='causal',state='completed',result_path=str(path),started_at='2026-10-26T12:00:00+00:00',finished_at='2026-10-26T13:00:00+00:00')
    assert evidence.prerequisite_fault(row,tmp_path) is None
    process=dict(args='uv pip install '+' '.join(sorted(evidence.PINNED)),elapsed='20:00',official_verifier_ancestors=[dict(pid=1)])
    data=dict(result_path=str(path),model_returncode=124,at='2026-10-26T12:59:00+00:00',installers=[process])
    proof=tmp_path/'codex-causal-proof.json';proof.write_text(json.dumps(data))
    assert evidence.prerequisite_fault(row,tmp_path)['installer_elapsed_s']==1200
    for change in (dict(prerequisite_cache=dict(cache_sha256='warm')),dict(status='task_or_model_failed'),dict(official_tests_rc=1)):
        path.write_text(json.dumps({**result,**change}));assert evidence.prerequisite_fault(row,tmp_path) is None
    path.write_text(json.dumps(result))
    process['official_verifier_ancestors']=[];proof.write_text(json.dumps(data));assert evidence.prerequisite_fault(row,tmp_path) is None
    process['official_verifier_ancestors']=[dict(pid=1)];process['elapsed']='05:00';proof.write_text(json.dumps(data));assert evidence.prerequisite_fault(row,tmp_path) is None


def test_one_repair_per_cell_and_running_attempt_is_preserved(tmp_path,monkeypatch):
    first=dict(task='hf-train-lora-adapter',harness='codex',policy='causal',expected_context='compact',state='completed',result_path=str(tmp_path/'original'))
    cells=[first];retries=[];key=lambda r:(r['task'],r['harness'],r['policy'])
    monkeypatch.setattr(evidence,'prerequisite_fault',lambda r:dict(verified=True))
    added=evidence.register_prerequisite_repairs(cells,retries,'repair',lambda p:'hash',key)
    assert len(added)==1
    assert evidence.register_prerequisite_repairs(cells,retries,'next',lambda p:'hash',key)==[]
    retries[0].update(state='running');assert evidence.register_prerequisite_repairs(cells,retries,'next',lambda p:'hash',key)==[]
