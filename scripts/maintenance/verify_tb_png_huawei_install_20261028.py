"""Check PNG's exact verifier prerequisites in a disposable native transaction."""
import datetime
import json
import os
from pathlib import Path
import subprocess

DATA = Path('/mnt/data/agenttx-runtime')
AUDIT = DATA/'validation/tb-huawei-transport-20261028'


def main():
    active=json.loads((DATA/'validation/active-tb-dual-native.json').read_text())
    progress=json.loads((Path(active['root'])/'progress.json').read_text())
    original=next(r for r in progress['tasks'] if r['task']=='png-generation' and
                  r['harness']=='sweagent' and r['policy']=='clean_recovery' and r['state']=='completed')
    source=Path(original['result_path']).parent
    live=next(r for r in progress['tasks']+progress['retry_tasks'] if r['state']=='running' and
              r['task']=='png-generation' and r['harness']=='sweagent' and r['policy']=='whole_branch_abort')
    network=json.loads((Path(live['result_path']).parent/'task-services/download-proxy.json').read_text())['network']
    folder=AUDIT/('native-install-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(parents=True)
    command=json.loads((source/'container-command.json').read_text())
    command=command[:command.index('--entrypoint')+3]
    command[command.index('--network')+1]=network
    command[command.index('--cidfile')+1]=str(folder/'container.cid')
    replacements=0
    for i,value in enumerate(command):
        if value == 'type=bind,src='+str(source)+',dst=/results' or value.startswith('type=bind,src='+str(source)+',dst=/results,'):
            command[i]=value.replace('src='+str(source),'src='+str(folder),1)
            replacements+=1
    assert replacements==1 and not any('src='+str(source)+',dst=/results' in v for v in command)
    assert command[command.index('--network')+1].startswith('container:')
    install='''set -eu
export UV_CACHE_DIR=/results/cache-huawei UV_HTTP_TIMEOUT=180
export UV_INDEX_URL=https://repo.huaweicloud.com/repository/pypi/simple
/tmp/agenttx-uv venv --python python3 /tmp/huawei-venv
/tmp/agenttx-uv pip install --python /tmp/huawei-venv/bin/python pytest requests psutil easyocr==1.7.2
/tmp/agenttx-uv pip check --python /tmp/huawei-venv/bin/python
/tmp/huawei-venv/bin/python -c 'import easyocr,torch,pytest; assert easyocr.__version__ == "1.7.2"; print(easyocr.__version__, torch.__version__, pytest.__version__)'
'''
    probe='\n'.join([
        'import json,pathlib,time',
        'from experiments.scripts.bench_tb_image_native import prepare_runtime_tools,prime_resolver',
        'from agenttx.harness import CodingAgentHarness',
        'from agenttx.policy import CommitPolicy',
        "prepare_runtime_tools(pathlib.Path('/results'))",
        "h=CodingAgentHarness(pathlib.Path('/app'),session_dir=pathlib.Path('/results/session'),trace_backend='strace',policy=CommitPolicy(pathlib.Path('/app'),allow_external_writes=True,ephemeral_roots=(pathlib.Path('/root'),pathlib.Path('/tmp'))))",
        'prime_resolver(h)',
        'start=time.monotonic()',
        "r=h.tx.run_tool('cold-huawei-verifier-prerequisites',['bash','-c',"+repr(install)+"],trace_reads=False,timeout_s=240)",
        'result=dict(returncode=r.returncode,seconds=time.monotonic()-start,stdout=r.stdout,stderr=r.stderr)',
        "pathlib.Path('/results/install-result.json').write_text(json.dumps(result,indent=2)+chr(10))",
        'print(json.dumps(result))',
        'h.close(destroy=False)',
        'raise SystemExit(r.returncode)',
    ])
    compile(probe,'png-native-prerequisite-probe','exec')
    command+=['-c',probe]
    env=dict(os.environ,DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        r=subprocess.run(command,env=env,capture_output=True,text=True,timeout=300)
        rc,stdout,stderr=r.returncode,r.stdout,r.stderr
    except subprocess.TimeoutExpired as e:
        rc=124;stdout=(e.stdout or b'').decode();stderr=(e.stderr or b'').decode()
    finally:
        cidfile=folder/'container.cid'
        if cidfile.exists():subprocess.run(['docker','rm','-f',cidfile.read_text().strip()],env=env,capture_output=True,timeout=20)
    (folder/'probe.stdout').write_text(stdout);(folder/'probe.stderr').write_text(stderr)
    result=dict(at=started,finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                returncode=rc,no_model_calls=True,official_tests_executed=False,
                original_result_mount_replaced=True,running_workers_modified=False,
                host_forwarding_modified=False,network='existing_private_task_namespace',
                deployed_to_experiments=False,package_arguments='pytest requests psutil easyocr==1.7.2')
    (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(folder);print(json.dumps(result));print(stdout[-7000:]);print(stderr[-2000:])
    raise SystemExit(rc)


if __name__=='__main__':main()
