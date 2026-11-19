"""Exercise cold UV installation and fallback in a separate native transaction."""
import datetime
import json
import os
from pathlib import Path
import subprocess

PROJECT = Path(__file__).resolve().parents[2]
DATA = Path('/mnt/data/agenttx-runtime')
AUDIT = DATA/'validation/tb-wheel-timeout-fallback-20261028'


def main():
    active=json.loads((DATA/'validation/active-tb-dual-native.json').read_text())
    progress=json.loads((Path(active['root'])/'progress.json').read_text())
    original=next(r for r in progress['retry_tasks'] if r['task']=='play-zork' and
                  r['harness']=='sweagent' and r['policy']=='clean_recovery' and r['state']=='completed')
    source=Path(original['result_path']).parent
    live=next(r for r in progress['tasks']+progress['retry_tasks'] if r['state']=='running' and
              r['task']=='play-zork-easy' and r['harness']=='sweagent' and r['policy']=='ideal_oracle')
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
    assert replacements==1
    assert not any('src='+str(source)+',dst=/results' in v for v in command)
    at=command.index('--entrypoint')
    command[at:at]=['--mount','type=bind,src='+str(PROJECT/'experiments/scripts/tb_verifier_download_fallback.py')+',dst=/diagnostic-fallback.py,readonly']
    fallback_install=r'''set -eu
mkdir -p /tmp/diagnostic-bin
cat > /tmp/diagnostic-bin/uv <<'SH'
#!/bin/bash
if [ "$UV_INDEX_URL" = https://mirrors.aliyun.com/pypi/simple ]; then
 echo 'error: Failed to download simulated wheel' >&2
 echo 'Failed to download distribution due to network timeout.' >&2
 exit 2
fi
exec /tmp/agenttx-uv "$@"
SH
chmod +x /tmp/diagnostic-bin/uv
export PATH=/tmp/diagnostic-bin:$PATH
export UV_CACHE_DIR=/results/cache-fallback UV_HTTP_TIMEOUT=10
export UV_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
/tmp/agenttx-uv venv --python python3 /tmp/fallback-venv
'''
    verify='''
uv pip install --python /tmp/fallback-venv/bin/python pytest==8.4.1
/tmp/agenttx-uv pip check --python /tmp/fallback-venv/bin/python
/tmp/fallback-venv/bin/python -c 'import pytest; assert pytest.__version__ == "8.4.1"; print(pytest.__version__)'
'''
    original_install='''set -eu
export UV_CACHE_DIR=/results/cache-original UV_HTTP_TIMEOUT=10
/tmp/agenttx-uv venv --python python3 /tmp/original-venv
/tmp/agenttx-uv pip install --python /tmp/original-venv/bin/python --index-url https://mirrors.aliyun.com/pypi/simple pytest==8.4.1
'''
    probe='\n'.join([
        'import json,pathlib,runpy,subprocess',
        'from experiments.scripts.bench_tb_image_native import prepare_runtime_tools,prime_resolver',
        'from agenttx.harness import CodingAgentHarness',
        'from agenttx.policy import CommitPolicy',
        "prepare_runtime_tools(pathlib.Path('/results'))",
        "h=CodingAgentHarness(pathlib.Path('/app'),session_dir=pathlib.Path('/results/session'),trace_backend='strace',policy=CommitPolicy(pathlib.Path('/app'),allow_external_writes=True,ephemeral_roots=(pathlib.Path('/root'),pathlib.Path('/tmp'))))",
        'prime_resolver(h)',
        "shell=runpy.run_path('/diagnostic-fallback.py')['SHELL']",
        'results={}',
        'try:',
        " r=h.tx.run_tool('original-cold-install',['bash','-c',"+repr(original_install)+"],trace_reads=False,timeout_s=45)",
        " results['original']=dict(returncode=r.returncode,stdout=r.stdout,stderr=r.stderr)",
        'except subprocess.TimeoutExpired:',
        " results['original']=dict(returncode=124,diagnostic_timeout_s=45)",
        "r=h.tx.run_tool('fallback-cold-install',['bash','-c',"+repr(fallback_install)+"+chr(10)+shell+chr(10)+"+repr(verify)+"],trace_reads=False,timeout_s=60)",
        "results['fallback']=dict(returncode=r.returncode,stdout=r.stdout,stderr=r.stderr)",
        "results['fallback_events']=[json.loads(l) for l in r.stderr.splitlines() if l.startswith('{\"contract\":')]",
        "assert r.returncode==0 and results['fallback_events'], results",
        "pathlib.Path('/results/probe-results.json').write_text(json.dumps(results,indent=2)+chr(10))",
        'print(json.dumps(results))',
        'h.close(destroy=False)',
    ])
    compile(probe,'native-prerequisite-probe','exec')
    command+=['-c',probe]
    env=dict(os.environ,DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        r=subprocess.run(command,env=env,capture_output=True,text=True,timeout=140)
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
                deployed_to_experiments=False)
    (folder/'result.json').write_text(json.dumps(result,indent=2)+'\n')
    print(folder);print(json.dumps(result));print(stdout[-5000:]);print(stderr[-2000:])
    raise SystemExit(rc)


if __name__=='__main__':main()
