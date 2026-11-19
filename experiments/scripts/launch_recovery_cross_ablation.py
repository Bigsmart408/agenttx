#!/usr/bin/env python3
"""Launch the registered filesystem/context factorial coordinator in its privileged namespace."""
import argparse
import json
import os
from pathlib import Path


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=('preflight','run'))
    p.add_argument('--cohort',type=Path,required=True)
    args=p.parse_args()
    cohort=args.cohort.resolve()
    source=Path(json.loads((cohort/'registration.json').read_text())['frozen_source'])
    python='/home/bfq/.venvs/agenttx/bin/python'
    name='atx-'+cohort.name+'-'+args.action
    command=['docker','--host','unix:///run/agenttx-docker/docker.sock','run','--rm','--pull=never',
        '--privileged','--name',name,'--network=host','--tmpfs','/root:exec','--tmpfs','/tmp:exec',
        '--tmpfs','/sys','--tmpfs','/run','--workdir',str(source)]
    for path in ('/usr','/bin','/sbin','/lib','/lib64','/etc','/home/bfq'):
        recursive = ',bind-recursive=disabled' if path == '/home/bfq' else ''
        command+=['--mount',f'type=bind,src={path},dst={path},readonly{recursive}']
    for path in ('/mnt/data','/run/agenttx-docker/docker.sock',str(cohort)):
        recursive = ',bind-recursive=disabled' if path == '/mnt/data' else ''
        command+=['--mount',f'type=bind,src={path},dst={path}{recursive}']
    profile='/home/bfq/.config/agenttx/experiment-20261017'
    codex='/home/bfq/.local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex'
    env=dict(GIT_CONFIG_COUNT='1',GIT_CONFIG_KEY_0='safe.directory',GIT_CONFIG_VALUE_0='*',
        PATH='/home/bfq/.venvs/agenttx/bin:/home/bfq/.local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
        PYTHONPATH=f'{source}/src:{source}',PYTHONDONTWRITEBYTECODE='1',
        DOCKER_HOST='unix:///run/agenttx-docker/docker.sock',DOCKER_CONFIG='/mnt/data/agenttx-runtime/docker-cli',
        AGENTTX_DATA_ROOT='/mnt/data/agenttx-runtime',AGENTTX_CACHE_DIR='/mnt/data/agenttx-runtime/cache',
        AGENTTX_RESULTS_DIR=str(cohort.parent),AGENTTX_LOGS_DIR=str(cohort/'logs'),
        AGENTTX_DOWNLOAD_PROXY_DEFAULT='1',AGENTTX_LOCAL_ONLY='1',AGENTTX_DOMESTIC_ONLY='1',AGENTTX_SKIP_ORPHAN_REAP='1',
        CODEX_BIN=codex,CODEX_CONFIG_FILE=profile+'/config.toml',CODEX_AUTH_FILE=profile+'/auth.json',
        AGENTTX_SWE_PROFILE=profile+'/sweagent.toml',
        AGENTTX_SWE_AGENT_ROOT='/home/bfq/agenttx-workspace/agenttx-deps/SWE-agent-3ea751c087f32b16e039a2233dd6eefecef325d5',
        AGENTTX_HTTPBIN_IMAGE='sha256:5197bbb61a81b10d093842cacded095afa6a9ecf904109e1173abb16283b473f',
        AGENTTX_RANDOMIZE_CRASH='1',AGENTTX_FAULT_SEED='20261018',AGENTTX_PRECRASH_REFERENCE_PROGRESS='0',
        AGENTTX_RECOVERY_CONTEXT='full',AGENTTX_SWE_CONTEXT_PROFILE='uniform',AGENTTX_SWE_INCLUDE_IDEAL='1',
        AGENTTX_SWE_VERIFY='docker',AGENTTX_NO_PROXY='1',NO_PROXY='*',
        TMPDIR=str(cohort/'scratch'),AGENTTX_TRACE_SPOOL_ROOT=str(cohort/'scratch'),
        TIKTOKEN_CACHE_DIR='/mnt/data/agenttx-runtime/tiktoken-cache',
        PIP_CACHE_DIR='/mnt/data/agenttx-runtime/pip-cache',UV_CACHE_DIR='/mnt/data/agenttx-runtime/uv-cache')
    for key,value in env.items():
        command+=['--env',key+'='+value]
    command+=['--entrypoint',python,'sha256:22feb19c04e4b8e8439ecafdfa0cba6430c415e0a8edea86c10e706a2646fed7','-u',
        str(source/'experiments/scripts/run_recovery_cross_ablation.py'),args.action,'--cohort',str(cohort)]
    (cohort/(args.action+'-container-command.json')).write_text(json.dumps(command,indent=2)+'\n')
    os.execvp(command[0],command)


if __name__=='__main__':
    main()
