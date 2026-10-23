"""Fresh, pinned TB service stacks with the original client data mounts."""
from pathlib import Path
import copy
import json
import shutil
import subprocess
import uuid
import yaml

from experiments.scripts.validate_tb_ready_local import DATA, digest, save

# Pinned after the domestic-mirror-only helper build; no model proxy/listener.
NETWORK_HELPER_IMAGE='sha256:10b423d23527e36da72c554fa82426553a0a8474926e5dcc80048dda358baf0c'
USERMODE_START=r'''
import os,subprocess,sys
pid=int(sys.argv[1]);assert pid>1
assert os.readlink('/proc/self/ns/net')!=os.readlink('/proc/%d/ns/net'%pid)
# Only the target private namespace is changed; host firewall is never written.
subprocess.run(['nsenter','-t',str(pid),'-n','--','ip','route','del','default'],capture_output=True)
os.execvp('slirp4netns',['slirp4netns','--configure','--mtu=1500','--disable-host-loopback',str(pid),'tap0'])
'''


def service_evidence(source, task):
    compose=source/'docker-compose.yaml'
    if not compose.exists() or len(yaml.safe_load(compose.read_text())['services'])<=1:
        return None
    matches=[]
    for path in (DATA/'validation').glob('tb-sidecar*/'+task+'/result.json'):
        row=json.loads(path.read_text())
        if (row.get('passed') and row.get('original_compose_sha256')==digest(compose)
                and (path.parent/'compose.yaml').is_file()
                and row.get('effective_compose_sha256')==digest(path.parent/'compose.yaml')):
            matches.append((row.get('finished_at',''),path))
    if not matches:
        raise RuntimeError('No verified service stack for '+task)
    return max(matches)[1]


class TaskServices:
    def __init__(self, source, task, folder, image_id):
        self.evidence=service_evidence(source,task)
        self.folder=Path(folder)/'task-services'
        self.command=None
        self.network=None
        self.client_id=None
        self.client={}
        self.egress=None
        self.anchor_id=None
        self.egress_id=None
        self.standalone_network=False
        self.image_id=image_id
        self.task=task

    def start(self):
        if self.evidence is None:
            self.folder.mkdir()
            try:
                if self.task=='create-bucket':
                    # The original LocalStack image ENTRYPOINT starts its S3
                    # daemon. Keep that original service outside tool process
                    # groups while the agent/verifier share its private netns.
                    image=json.loads(subprocess.check_output(['docker','image','inspect',self.image_id],text=True,timeout=30))[0]
                    assert image['Config']['Entrypoint']==['docker-entrypoint.sh']
                    command=['docker','run','-d','--rm','--pull=never','--network=none',
                        '--dns=10.0.2.3',self.image_id,'sh','-c','sleep infinity']
                else:
                    command=['docker','run','-d','--rm','--pull=never',
                        '--network=none','--dns=10.0.2.3','--entrypoint=/usr/bin/python3',NETWORK_HELPER_IMAGE,
                        '-c','import time;time.sleep(20000)']
                self.anchor_id=subprocess.check_output(command,text=True,timeout=30).strip()
                self._start_usermode(self.anchor_id)
                if self.task=='create-bucket':
                    self.client_id=self.anchor_id
                    self._wait_original_localstack()
            except Exception:
                self.close();raise
            return self
        self.folder.mkdir()
        prior=json.loads(self.evidence.read_text())
        config=yaml.safe_load((self.evidence.parent/'compose.yaml').read_text())
        client='client' if 'client' in config['services'] else 'webserver'
        assert config['services'][client]['image']==self.image_id
        shutil.copytree(self.evidence.parent/'runtime-assets',self.folder/'runtime-assets')
        old=str(self.evidence.parent/'runtime-assets');new=str(self.folder/'runtime-assets')
        for key,service in config['services'].items():
            assert service['image'].startswith('sha256:') and not service.get('build')
            assert not service.get('ports') and not service.get('container_name')
            subprocess.run(['docker','image','inspect',service['image']],check=True,
                           stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,timeout=30)
            service['volumes']=[v.replace(old,new,1) if v.startswith(old) else v
                                for v in service.get('volumes',[])]
        # Each cell owns its DNS namespace and volumes. Egress is needed for
        # official dependency installers and the model endpoint.
        config['networks']={'task':{'driver':'bridge','internal':False}}
        self.client=copy.deepcopy(config['services'][client])
        path=self.folder/'compose.yaml';path.write_text(yaml.safe_dump(config,sort_keys=False))
        project='agenttx-tb-'+uuid.uuid4().hex[:20]
        self.command=['docker','compose','--project-name',project,'-f',str(path)]
        save(self.folder/'registration.json',dict(task=self.task,project=project,
            source_evidence=str(self.evidence),source_evidence_sha256=digest(self.evidence),
            original_compose_sha256=prior['original_compose_sha256'],
            effective_compose_sha256=digest(path),fresh_volumes=True,
            host_ports_published=False,model_calls=0))
        try:
            with (self.folder/'up.log').open('w') as stream:
                subprocess.run([*self.command,'up','-d','--wait','--wait-timeout','150'],
                    check=True,stdout=stream,stderr=subprocess.STDOUT,timeout=200)
            self.client_id=subprocess.check_output([*self.command,'ps','-q',client],text=True,timeout=30).strip()
            assert self.client_id
            info=json.loads(subprocess.check_output(['docker','inspect',self.client_id],text=True,timeout=30))[0]
            networks=info['NetworkSettings']['Networks'];assert len(networks)==1
            self.network=next(iter(networks))
            self._start_usermode(self.client_id)
            save(self.folder/'runtime.json',dict(client_id=self.client_id,network=self.network,
                client_environment=self.client.get('environment',{}),host_network_namespace=False,
                egress='slirp4netns',host_firewall_modified=False))
        except Exception:
            self.close();raise
        return self

    def _wait_original_localstack(self):
        import time
        # Health only: never create sample-bucket or apply its public ACL.
        probe="import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:4566/_localstack/health',timeout=3).status)"
        last=None
        for _ in range(45):
            health=subprocess.run(['docker','run','--rm','--pull=never','--network='+self.network,
                '--entrypoint=/usr/bin/python3',NETWORK_HELPER_IMAGE,'-c',probe],capture_output=True,text=True,timeout=10)
            if health.returncode==0 and health.stdout.strip()=='200':
                save(self.folder/'original-service.json',dict(task=self.task,image_id=self.image_id,
                    original_entrypoint=['docker-entrypoint.sh'],health_status=200,task_solution_created=False,
                    service_outlives_tool_processes=True,private_network_namespace=True))
                return
            last=health.stderr[-2000:];time.sleep(1)
        raise RuntimeError('Original LocalStack service did not become healthy: '+str(last))

    def _start_usermode(self, anchor):
        import time
        info=json.loads(subprocess.check_output(['docker','inspect',anchor],text=True,timeout=30))[0]
        self.network='container:'+anchor
        self.egress_id=subprocess.check_output(['docker','run','-d','--rm','--pull=never',
            '--privileged','--pid=host','--network=host','--entrypoint=/usr/bin/python3',
            NETWORK_HELPER_IMAGE,'-c',USERMODE_START,str(info['State']['Pid'])],text=True,timeout=30).strip()
        # This is a trusted network-stack helper, never the task container.
        # It opens ordinary outbound sockets and never listens on host ports.
        probe="import urllib.request,urllib.error\ntry:\n print(urllib.request.urlopen('http://10.249.44.127:8080/v1/models',timeout=5).status)\nexcept urllib.error.HTTPError as e: print(e.code)"
        last=None
        for _ in range(10):
            ready=subprocess.run(['docker','run','--rm','--pull=never','--network='+self.network,
                '--entrypoint=/usr/bin/python3',NETWORK_HELPER_IMAGE,'-c',probe],
                capture_output=True,text=True,timeout=15)
            if ready.returncode==0 and ready.stdout.strip() in {'200','401'}:break
            last=ready.stderr[-2000:];time.sleep(.5)
        else:raise RuntimeError('Isolated outbound network failed its API reachability check: '+str(last))
        save(self.folder/'egress.json',dict(mode='slirp4netns',anchor_id=anchor,
            helper_id=self.egress_id,network=self.network,host_network_namespace=False,
            host_firewall_modified=False,host_ports_published=False,endpoint_proxy=False))
        save(self.folder/'runtime.json',dict(network=self.network,host_network_namespace=False,
            task=self.task,egress='slirp4netns',host_firewall_modified=False))

    def apply(self, command):
        assert self.network and self.network.startswith('container:')
        if self.evidence is not None:assert self.client_id
        result=list(command)
        if '--network' in result:
            index=result.index('--network');result[index+1]=self.network
        else:
            index=next(i for i,value in enumerate(result) if value.startswith('--network='))
            result[index]='--network='+self.network
        options=['--volumes-from',self.client_id] if self.client_id else []
        for key,value in self.client.get('environment',{}).items():
            options+=['--env',key+'='+str(value)]
        # Docker shares the anchor's hosts/resolv.conf in container network mode.
        return result[:2]+options+result[2:]

    def close(self):
        if self.anchor_id and self.task=='create-bucket':
            logs=subprocess.run(['docker','logs',self.anchor_id],capture_output=True,text=True,timeout=15)
            (self.folder/'original-service.log').write_text(logs.stdout+logs.stderr)
        for cid in (self.egress_id,self.anchor_id):
            if cid:subprocess.run(['docker','rm','-f',cid],capture_output=True,text=True,timeout=30)
        self.egress_id=None;self.anchor_id=None
        if self.command is None:return
        try:
            result=subprocess.run([*self.command,'logs','--no-color'],capture_output=True,text=True,timeout=30)
            (self.folder/'services.log').write_text(result.stdout+result.stderr)
        finally:
            result=subprocess.run([*self.command,'down','--volumes','--remove-orphans','--timeout','5'],
                                  capture_output=True,text=True,timeout=60)
            save(self.folder/'cleanup.json',dict(returncode=result.returncode,stdout=result.stdout,stderr=result.stderr))
