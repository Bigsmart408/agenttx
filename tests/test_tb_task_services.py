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
    stack.network='container:owned-client';stack.client_id='owned-client'
    stack.client=dict(environment=dict(DATABASE_URL='postgresql://db:5432/task'),
                      extra_hosts=['homeserver.local:127.0.0.1'])
    command=['docker','run','--network','host','--mount','type=bind,src=/code,dst=/code,readonly','image','worker']
    result=stack.apply(command)
    assert result[result.index('--network')+1]=='container:owned-client'
    assert result[result.index('--volumes-from')+1]=='owned-client'
    assert 'DATABASE_URL=postgresql://db:5432/task' in result
    assert '--add-host' not in result  # Already on the shared anchor's hosts file.
    assert command[command.index('--network')+1]=='host'
    assert result[-2:]==['image','worker']
    equals=stack.apply(['docker','run','--network=host','image','worker'])
    assert '--network=container:owned-client' in equals and '--network=host' not in equals


def test_standalone_task_never_inherits_host_network():
    stack=object.__new__(services.TaskServices)
    stack.evidence=None
    stack.network='container:isolated-anchor'
    stack.client_id=None;stack.client={}
    result=stack.apply(['docker','run','--privileged','--network','none','image','worker'])
    assert result[result.index('--network')+1]==stack.network
    assert '--volumes-from' not in result
    assert 'host' not in result


def test_usermode_failure_cleans_only_owned_containers(tmp_path,monkeypatch):
    monkeypatch.setattr(services,'service_evidence',lambda *a:None)
    monkeypatch.setattr(subprocess,'check_output',lambda *a,**k:'owned-anchor\n')
    calls=[]
    monkeypatch.setattr(subprocess,'run',lambda cmd,**kw:calls.append(cmd))
    monkeypatch.setattr(services.TaskServices,'_start_usermode',lambda *a:(_ for _ in ()).throw(RuntimeError('failed network')))
    stack=services.TaskServices(tmp_path,'example',tmp_path,'sha256:example')
    with pytest.raises(RuntimeError):stack.start()
    assert calls==[['docker','rm','-f','owned-anchor']]


def test_network_start_script_never_writes_host_firewall():
    assert 'iptables' not in services.USERMODE_START
    assert '8080' not in services.USERMODE_START
    assert "'/proc/self/ns/net'" in services.USERMODE_START
    assert 'slirp4netns' in services.USERMODE_START


def test_localstack_keeps_original_entrypoint_outside_tool_processes(tmp_path,monkeypatch):
    monkeypatch.setattr(services,'service_evidence',lambda *a:None)
    calls=[]
    def output(command,**kw):
        calls.append(command)
        if command[:3]==['docker','image','inspect']:
            return json.dumps([dict(Config=dict(Entrypoint=['docker-entrypoint.sh']))])
        return 'owned-localstack-anchor\n'
    monkeypatch.setattr(subprocess,'check_output',output)
    monkeypatch.setattr(services.TaskServices,'_start_usermode',lambda self,anchor:setattr(self,'network','container:'+anchor))
    checked=[]
    monkeypatch.setattr(services.TaskServices,'_wait_original_localstack',lambda self:checked.append(self.anchor_id))
    stack=services.TaskServices(tmp_path,'create-bucket',tmp_path,'sha256:original-task-image').start()
    command=next(c for c in calls if c[:2]==['docker','run'])
    assert '--entrypoint' not in command and '--entrypoint=/usr/bin/python3' not in command
    assert 'sha256:original-task-image' in command
    assert stack.client_id==stack.anchor_id=='owned-localstack-anchor'
    assert checked==['owned-localstack-anchor']
    assert 'sample-bucket' not in ' '.join(command)
