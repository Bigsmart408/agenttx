import sys,json,subprocess,datetime
from pathlib import Path
sys.path[:0]=['/home/bfq/agenttx','/home/bfq/agenttx/src']
from experiments.scripts.tb_task_services import TaskServices,NETWORK_HELPER_IMAGE
root=Path('/mnt/data/agenttx-runtime/validation/tb-api-connection-repair-20261023')
folder=root/('sidecar-probe-'+datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S'));folder.mkdir()
source=Path('/mnt/data/agenttx-runtime/cache/terminal_bench/terminal-bench-1/original-tasks/interactive-maze-game')
# Select the same verified evidence/image as production.
from experiments.scripts.tb_task_services import service_evidence
import yaml
evidence=service_evidence(source,'interactive-maze-game');config=yaml.safe_load((evidence.parent/'compose.yaml').read_text());image=config['services']['client']['image']
stack=TaskServices(source,'interactive-maze-game',folder,image).start()
try:
 code="import socket,urllib.request,urllib.error;ip=socket.gethostbyname('maze');s=socket.create_connection((ip,8088),timeout=5);s.close();print('sidecar_ok')"
 proof=subprocess.check_output(['docker','run','--rm','--pull=never','--network='+stack.network,'--entrypoint=/usr/bin/python3',NETWORK_HELPER_IMAGE,'-c',code],text=True,timeout=15)
 assert proof.strip()=='sidecar_ok'
 (root/'sidecar-isolation-verification.json').write_text(json.dumps(dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),task='interactive-maze-game',original_sidecar_dns_and_tcp=True,private_api_reachable=True,host_firewall_modified=False,no_model_calls=True),indent=2)+'\n');print(proof.strip())
finally:stack.close()
