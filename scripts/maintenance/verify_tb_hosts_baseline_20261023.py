from pathlib import Path
import sys,json,subprocess,datetime
sys.path[:0]=['/home/bfq/agenttx','/home/bfq/agenttx/src']
from experiments.scripts.run_tb_dual_local import cell_command
r=Path('/mnt/data/agenttx-runtime/validation/tb-api-connection-repair-20261023')
harness=sys.argv[1]
folder=r/('hosts-baseline-'+harness+'-'+datetime.datetime.now(datetime.timezone.utc).strftime('%H%M%S'))
row=dict(task='create-bucket',harness=harness,policy='clean_recovery')
evidence='/mnt/data/agenttx-runtime/validation/tb-basic-official-validation-20261019-r1/create-bucket/result.json'
cmd=cell_command(Path('/home/bfq/agenttx'),folder,row,evidence)+['--preflight-only']
with (r/('hosts-baseline-'+harness+'.log')).open('w') as out:
 p=subprocess.run(cmd,stdout=out,stderr=subprocess.STDOUT,timeout=240)
path=folder/'clean_recovery/host-result.json';assert path.exists(),str(folder)
result=json.loads(path.read_text());assert result.get('status')=='preflight_passed',str(result.get('error'))
assert result['network_baseline']['localhost_resolution']
proof=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),harness=harness,task='create-bucket',result_path=str(path),network_baseline=result['network_baseline'],adapter_import=result['adapter_import_preflight'],no_model_calls=True)
(r/('hosts-baseline-'+harness+'-verification.json')).write_text(json.dumps(proof,indent=2)+'\n');print(json.dumps(proof))
