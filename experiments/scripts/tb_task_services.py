"""Fresh, pinned TB service stacks with the original client data mounts."""
from pathlib import Path
import copy
import json
import shutil
import subprocess
import uuid
import yaml

from experiments.scripts.validate_tb_ready_local import DATA, digest, save

NETWORK_HELPER_IMAGE='sha256:ef8aa38c0d00b9d33be678a4a3af1271adea71b1e66bf9b092373eceee831187'
NETWORK_RULES=r'''
import ipaddress,json,re,subprocess,sys
action,subnet,bridge,label=sys.argv[1:]
network=ipaddress.ip_network(subnet,strict=True)
assert network.version==4 and network.is_private and network.prefixlen>=16
assert re.fullmatch(r'br-[0-9a-f]{12}',bridge)
assert re.fullmatch(r'agenttx-tb-[0-9a-f]{20}_task',label)
assert action in {'add','remove'}
comment=['-m','comment','--comment',label]
rules=[['-t','nat','POSTROUTING','-s',subnet,'!','-o',bridge,*comment,'-j','MASQUERADE'],
       ['-t','filter','FORWARD','-i',bridge,*comment,'-j','ACCEPT'],
       ['-t','filter','FORWARD','-o',bridge,'-m','conntrack','--ctstate','RELATED,ESTABLISHED',*comment,'-j','ACCEPT']]
changed=[]
try:
 for tableflag,table,chain,*rule in rules:
  prefix=['/usr/sbin/iptables','-w','10',tableflag,table]
  exists=subprocess.run([*prefix,'-C',chain,*rule],capture_output=True).returncode==0
  if action=='add' and not exists:
   subprocess.run([*prefix,'-I',chain,'1',*rule],check=True);changed.append([*prefix,'-D',chain,*rule])
  elif action=='remove' and exists:subprocess.run([*prefix,'-D',chain,*rule],check=True)
except Exception:
 if action=='add':
  for command in reversed(changed):subprocess.run(command,capture_output=True)
 raise
print(json.dumps(dict(action=action,subnet=subnet,bridge=bridge,comment=label,rules=rules)))
'''


def configure_egress(action, subnet, bridge, network):
    # The dedicated daemon deliberately does not manage host firewall rules.
    # Enter the host only to add/remove rules scoped to this owned task bridge.
    command=['docker','run','--rm','--pull=never','--privileged','--pid=host','--network=host',
        '--entrypoint','/usr/bin/nsenter',NETWORK_HELPER_IMAGE,'-t','1','-m','-n','--',
        '/usr/bin/python3','-c',NETWORK_RULES,action,subnet,bridge,network]
    result=subprocess.run(command,check=True,capture_output=True,text=True,timeout=45)
    return json.loads(result.stdout)


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
        self.image_id=image_id
        self.task=task

    def start(self):
        if self.evidence is None:return self
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
            network_info=json.loads(subprocess.check_output(['docker','network','inspect',self.network],
                                     text=True,timeout=30))[0]
            subnet=network_info['IPAM']['Config'][0]['Subnet']
            bridge='br-'+network_info['Id'][:12]
            self.egress=configure_egress('add',subnet,bridge,self.network)
            save(self.folder/'egress.json',self.egress)
            save(self.folder/'runtime.json',dict(client_id=self.client_id,network=self.network,
                client_environment=self.client.get('environment',{})))
        except Exception:
            self.close();raise
        return self

    def apply(self, command):
        if self.evidence is None:return command
        assert self.network and self.client_id
        result=list(command)
        if '--network' in result:
            index=result.index('--network');result[index+1]=self.network
        else:
            index=next(i for i,value in enumerate(result) if value.startswith('--network='))
            result[index]='--network='+self.network
        options=['--volumes-from',self.client_id]
        for key,value in self.client.get('environment',{}).items():
            options+=['--env',key+'='+str(value)]
        for value in self.client.get('extra_hosts',[]):options+=['--add-host',value]
        return result[:2]+options+result[2:]

    def close(self):
        if self.command is None:return
        try:
            result=subprocess.run([*self.command,'logs','--no-color'],capture_output=True,text=True,timeout=30)
            (self.folder/'services.log').write_text(result.stdout+result.stderr)
        finally:
            if self.egress:
                try:
                    save(self.folder/'egress-cleanup.json',configure_egress('remove',
                        self.egress['subnet'],self.egress['bridge'],self.network))
                    self.egress=None
                except Exception as exc:
                    save(self.folder/'egress-cleanup-error.json',dict(error=repr(exc),rules=self.egress))
            result=subprocess.run([*self.command,'down','--volumes','--remove-orphans','--timeout','5'],
                                  capture_output=True,text=True,timeout=60)
            save(self.folder/'cleanup.json',dict(returncode=result.returncode,
                stdout=result.stdout,stderr=result.stderr))
