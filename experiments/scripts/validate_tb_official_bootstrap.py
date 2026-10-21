#!/usr/bin/env python3
"""No-model negative/positive verifier checks from public TB task instructions."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from experiments.scripts.validate_tb_ready_local import classify_junit, save, digest

DATA = Path('/mnt/data/agenttx-runtime')
SETUP = r'''#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
unset UV_DEFAULT_INDEX
export UV_PYTHON_DOWNLOADS=never
export TEST_DIR=/tests
unset PYTHONPATH
for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do
    [ -f "$source" ] || continue
    sed -i -e 's|http://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
           -e 's|http://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
           -e 's|http://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
           -e 's|http://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' "$source"
done
printf 'Acquire::Retries "0"; Acquire::http::Timeout "8"; Acquire::https::Timeout "8";\n' > /etc/apt/apt.conf.d/99agenttx-validation
export PYTEST_ADDOPTS=--junitxml=/results/baseline.xml
set +e
bash /probe/effective-run-tests.sh > /results/baseline.stdout 2> /results/baseline.stderr
baseline_rc=$?
set -e
printf '%s\n' "$baseline_rc" > /results/baseline.rc
case "$AGENTTX_PUBLIC_INSTRUCTION_TASK" in
    hello-world) printf 'Hello, world!\n' > hello.txt ;;
    csv-to-parquet) .tbench-testing/bin/python -c "import pandas as pd; pd.read_csv('data.csv').to_parquet('data.parquet')" ;;
    *) exit 71 ;;
esac
export PYTEST_ADDOPTS=--junitxml=/results/positive.xml
set +e
bash /probe/effective-run-tests.sh > /results/positive.stdout 2> /results/positive.stderr
positive_rc=$?
set -e
printf '%s\n' "$positive_rc" > /results/positive.rc
'''


def run(task_name, output, evidence):
    from experiments.workloads import terminal_bench_suite as tb
    tools = DATA/'validation/tb-validation-tools-20261018'
    task = tb.load_tasks(DATA/'cache')[task_name]
    source = tb.task_source(DATA/'cache', task)
    folder = output/task_name
    folder.mkdir()
    record = next(r for r in evidence['checks'] if r['task']==task_name and r['identity_verified'])
    original = (source/'run-tests.sh').read_text()
    install = 'curl -LsSf https://astral.sh/uv/0.7.13/install.sh | sh'
    assert original.count(install)==1
    # Only the bootstrap transport changes. The uv version, dependency pins,
    # interpreter selection and official test invocation remain upstream's.
    replacement = 'mkdir -p "$HOME/.local/bin" && install -m755 /probe/uv "$HOME/.local/bin/uv" && printf \'export PATH="$HOME/.local/bin:$PATH"\\n\' > "$HOME/.local/bin/env"'
    effective = original.replace(install, replacement)
    (folder/'effective-run-tests.sh').write_text(effective)
    (folder/'run.sh').write_text(SETUP)
    name = 'agenttx-tb-bootstrap-' + uuid.uuid4().hex
    command = ['docker','run','--rm','--pull=never','--name',name,'--network=host',
               '--cpus=1','--memory=3g','--mount',f'type=bind,src={folder},dst=/results',
               '--mount',f'type=bind,src={source}/tests,dst=/tests,readonly',
               '--mount',f'type=bind,src={folder}/effective-run-tests.sh,dst=/probe/effective-run-tests.sh,readonly',
               '--mount',f'type=bind,src={folder}/run.sh,dst=/probe/run.sh,readonly',
               '--mount',f'type=bind,src={tools}/bin/uv,dst=/probe/uv,readonly',
               '-e','AGENTTX_PUBLIC_INSTRUCTION_TASK='+task_name,
               '--entrypoint','bash',record['local_image_id'],'/probe/run.sh']
    save(folder/'command.json', command)
    row = dict(task=task_name, started_at=datetime.now(timezone.utc).isoformat(),
        image_id=record['local_image_id'], model_calls=0, gold_solution_used=False,
        original_script_sha256=digest(source/'run-tests.sh'), effective_script_sha256=digest(folder/'effective-run-tests.sh'),
        uv_sha256=digest(tools/'bin/uv'), uv_version='0.7.13',
        test_assets={str(p.relative_to(source/'tests')):digest(p) for p in (source/'tests').rglob('*') if p.is_file()},
        transport_changes=['Local version-matched uv binary replaces installer download.',
                           'APT/Python package URLs use domestic mirrors in disposable container.'],
        positive_fixture='Public task instruction implemented directly; no solution.sh accessed.')
    start = time.monotonic()
    try:
        with (folder/'container.stdout').open('w') as out, (folder/'container.stderr').open('w') as err:
            result = subprocess.run(command, stdout=out, stderr=err, timeout=300)
        row['container_returncode'] = result.returncode
    except subprocess.TimeoutExpired:
        row['container_returncode'] = 124
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=20)
    for label in ['baseline','positive']:
        path = folder/(label+'.rc')
        rc = int(path.read_text()) if path.exists() else None
        row[label+'_returncode'] = rc
        row[label+'_classification'], row[label+'_tests'] = classify_junit(folder/(label+'.xml'),rc)
    row['passed'] = (row['container_returncode']==0 and row['baseline_classification']=='baseline_failed'
                     and row['positive_classification']=='baseline_passed')
    row['wall_s'] = time.monotonic()-start
    save(folder/'result.json',row)
    print(json.dumps(row),flush=True)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    args.output.mkdir(exist_ok=False)
    tasks = ['hello-world','csv-to-parquet']
    evidence = json.loads((DATA/'validation/tb-readiness-recheck-20261018.json').read_text())
    save(args.output/'registration.json',dict(tasks=tasks,model_calls=0,gold_solution_used=False,
        container_timeout_s=300,source_script_sha256=digest(Path(__file__)),
        purpose='Verify actual upstream bootstrap and tests with public negative/positive fixtures.'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(lambda task:run(task,args.output,evidence),tasks))
    save(args.output/'execution.json',dict(finished_at=datetime.now(timezone.utc).isoformat(),
        passed=sum(r['passed'] for r in rows),attempted=len(rows),model_calls=0,tasks=rows))


if __name__=='__main__':
    main()
