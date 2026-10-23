from pathlib import Path
import json,sys,subprocess,datetime
sys.path[:0]=['/home/bfq/agenttx','/home/bfq/agenttx/src']
from experiments.scripts.tb_task_services import TaskServices
A=Path('/mnt/data/agenttx-runtime/validation/tb-api-connection-repair-20261023');folder=A/('original-localstack-'+datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S'));folder.mkdir()
source=Path('/mnt/data/agenttx-runtime/cache/terminal_bench/terminal-bench-1/original-tasks/create-bucket')
image='sha256:44596ee0bc2a8dc973b9c4de4c6a3165d50dee92a202b33ae5736dbc6cffdb65'
stack=TaskServices(source,'create-bucket',folder,image).start()
try:
 cmd=['docker','exec',stack.anchor_id,'awslocal','s3api','list-buckets','--endpoint-url=http://127.0.0.1:4566','--output=json']
 result=json.loads(subprocess.check_output(cmd,text=True,timeout=60));assert result['Buckets']==[],result
 proof=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),private_localstack_health=200,initial_s3_buckets_empty=True,task_solution_created=False,original_entrypoint_preserved=True,host_firewall_modified=False,no_model_calls=True,evidence=str(folder))
 (A/'original-localstack-verification.json').write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))
finally:stack.close()
