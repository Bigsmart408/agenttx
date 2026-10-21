import json
from pathlib import Path
import pytest
import subprocess
import sys

from experiments.scripts import tb_task_services as services


def test_service_evidence_rejects_modified_source_or_effective_compose(tmp_path,monkeypatch):
    monkeypatch.setattr(services,'DATA',tmp_path)
    source=tmp_path/'source';source.mkdir()
    original='services:\n  client: {}\n  db: {}\n'
    (source/'docker-compose.yaml').write_text(original)
    folder=tmp_path/'validation/tb-sidecar-proof/example';folder.mkdir(parents=True)
    (folder/'compose.yaml').write_text(original)
    row=dict(passed=True,original_compose_sha256=services.digest(source/'docker-compose.yaml'),
             effective_compose_sha256=services.digest(folder/'compose.yaml'),finished_at='2026-10-20')
    proof=folder/'result.json';proof.write_text(json.dumps(row))
    assert services.service_evidence(source,'example')==proof
    (folder/'compose.yaml').write_text(original+'# modified\n')
    with pytest.raises(RuntimeError,match='No verified service stack'):
        services.service_evidence(source,'example')
    (folder/'compose.yaml').write_text(original)
    (source/'docker-compose.yaml').write_text(original+'# changed upstream\n')
    with pytest.raises(RuntimeError,match='No verified service stack'):
        services.service_evidence(source,'example')


def test_service_attachment_uses_owned_network_and_original_data_volumes():
    stack=object.__new__(services.TaskServices)
    stack.evidence=Path('/verified/evidence.json')
    stack.network='private-unique-task-network';stack.client_id='owned-client'
    stack.client=dict(environment=dict(DATABASE_URL='postgresql://db:5432/task'),
                      extra_hosts=['homeserver.local:127.0.0.1'])
    command=['docker','run','--network','host','--mount','type=bind,src=/code,dst=/code,readonly','image','worker']
    result=stack.apply(command)
    assert result[result.index('--network')+1]=='private-unique-task-network'
    assert result[result.index('--volumes-from')+1]=='owned-client'
    assert 'DATABASE_URL=postgresql://db:5432/task' in result
    assert 'homeserver.local:127.0.0.1' in result
    assert command[command.index('--network')+1]=='host'
    assert result[-2:]==['image','worker']
    equals=stack.apply(['docker','run','--network=host','image','worker'])
    assert '--network=private-unique-task-network' in equals and '--network=host' not in equals


def test_task_firewall_is_scoped_and_rolls_back_partial_addition(monkeypatch):
    calls=[]
    def run(command,**kwargs):
        calls.append(command)
        if '-C' in command:return subprocess.CompletedProcess(command,1)
        if '-I' in command and 'FORWARD' in command:raise RuntimeError('injected firewall failure')
        return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(subprocess,'run',run)
    monkeypatch.setattr(sys,'argv',['rules','add','172.18.0.0/16','br-0123456789ab','agenttx-tb-01234567890123456789_task'])
    with pytest.raises(RuntimeError,match='injected firewall failure'):
        exec(services.NETWORK_RULES,{})
    mutations=[c for c in calls if '-I' in c or '-D' in c]
    assert [next(x for x in c if x in {'-I','-D'}) for c in mutations]==['-I','-I','-D']
    assert all('--comment' in c and 'agenttx-tb-01234567890123456789_task' in c for c in mutations)
    assert '-s' in mutations[-1] and '172.18.0.0/16' in mutations[-1]
    assert all('-F' not in c and '-P' not in c for c in calls)


@pytest.mark.parametrize('subnet,bridge,label',[
    ('0.0.0.0/0','br-0123456789ab','agenttx-tb-01234567890123456789_task'),
    ('172.18.0.0/16','docker0','agenttx-tb-01234567890123456789_task'),
    ('172.18.0.0/16','br-0123456789ab','unowned-network')])
def test_task_firewall_rejects_unowned_or_global_scope(monkeypatch,subnet,bridge,label):
    calls=[]
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:calls.append(a))
    monkeypatch.setattr(sys,'argv',['rules','add',subnet,bridge,label])
    with pytest.raises(AssertionError):exec(services.NETWORK_RULES,{})
    assert not calls
