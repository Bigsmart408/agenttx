from pathlib import Path
import sys,json,subprocess,urllib.request,urllib.error,datetime,os,re,tomllib
sys.path[:0]=['/home/bfq/agenttx','/home/bfq/agenttx/src']
from experiments.scripts.tb_task_services import TaskServices,NETWORK_HELPER_IMAGE
A=Path('/mnt/data/agenttx-runtime/validation/tb-api-connection-repair-20261023')
cell=A/('isolated-network-probe-'+datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S'));cell.mkdir()
source=cell/'source';source.mkdir(exist_ok=True)
stack=TaskServices(source,'network-isolation-probe',cell,NETWORK_HELPER_IMAGE).start()
cid=None
def host_rules():
    raw=subprocess.check_output(['docker','run','--rm','--pull','never','--privileged','--pid=host','--network=host',
        '--entrypoint','/usr/bin/nsenter',NETWORK_HELPER_IMAGE,'-t','1','-m','-n','--','/usr/sbin/iptables-save','-t','nat'],text=True)
    return '\n'.join(re.sub(r'\[\d+:\d+\]','[counters]',line) for line in raw.splitlines() if not line.startswith('#'))
def status():
    try:
        with urllib.request.urlopen('http://10.249.44.127:8080/v1/models',timeout=5) as r:return r.status
    except urllib.error.HTTPError as e:return e.code
try:
    cid=subprocess.check_output(['docker','run','-d','--rm','--pull','never','--privileged','--network',stack.network,
        '--entrypoint','/usr/bin/python3',NETWORK_HELPER_IMAGE,'-c','import time;time.sleep(300)'],text=True).strip()
    info=json.loads(subprocess.check_output(['docker','inspect',cid],text=True))[0]
    host_ns=os.readlink('/proc/self/ns/net');worker_ns=subprocess.check_output(['docker','exec',cid,'readlink','/proc/self/ns/net'],text=True).strip()
    assert host_ns!=worker_ns
    before=host_rules();host_before=status()
    def run(*args):return subprocess.run(['docker','exec',cid,*args],capture_output=True,text=True,check=True,timeout=15)
    probe='import urllib.request,urllib.error\ntry:\n r=urllib.request.urlopen("http://10.249.44.127:8080/v1/models",timeout=5);print(r.status)\nexcept urllib.error.HTTPError as e:print(e.code)\nexcept Exception as e:print(type(e).__name__)'
    container_before=run('/usr/bin/python3','-c',probe).stdout.strip();assert container_before=='401'
    # Verify private firewall ownership without touching any 8080 rule.
    run('/usr/sbin/iptables','-t','nat','-N','AGENTTX_ISOLATION_PROBE')
    container_after=run('/usr/bin/python3','-c',probe).stdout.strip();host_after=status();after=host_rules()
    assert container_after==str(host_before)==str(host_after)=='401'
    assert before==after,'Private firewall changed host NAT'
    run('/usr/sbin/iptables','-t','nat','-X','AGENTTX_ISOLATION_PROBE')
    config=Path('/home/bfq/.config/agenttx/experiment-20261017')
    auth=json.loads((config/'auth.json').read_text())['OPENAI_API_KEY']
    auth_probe="import sys,json,urllib.request;key=json.load(sys.stdin)['key'];r=urllib.request.urlopen(urllib.request.Request('http://10.249.44.127:8080/v1/models',headers={'Authorization':'Bearer '+key}),timeout=10);print(r.status)"
    private_auth=subprocess.run(['docker','exec','-i',cid,'/usr/bin/python3','-c',auth_probe],input=json.dumps({'key':auth}),capture_output=True,text=True,check=True,timeout=20)
    assert private_auth.stdout.strip()=='200'
    dns=run('/usr/bin/python3','-c',"import socket;print(socket.gethostbyname('mirrors.aliyun.com'))").stdout.strip();assert dns
    proof=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),host_netns=host_ns,container_netns=worker_ns,
        host_nat_unchanged=True,private_firewall_chain_isolated=True,
        forwarding_8080_unmodified=True,host_status_before=host_before,host_status_after=host_after,
        private_status_before=container_before,private_status_after=container_after,
        no_model_calls=True,private_authenticated_status=200,dns_resolved=True,network=stack.network)
    (A/'isolation-verification.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof,indent=2))
finally:
    if cid:subprocess.run(['docker','rm','-f',cid],capture_output=True,timeout=30)
    stack.close()
