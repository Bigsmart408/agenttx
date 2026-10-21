#!/usr/bin/env python3
"""Run original TB verifier bootstrap in fresh pinned images, without models or gold.

Installer transport and package mirrors change; explicit task-specific verifier
compatibility exceptions are recorded below. Tests are copied into the
container, since some official scripts move test assets or patch the workspace.
Neither these modified workspaces nor their images become agent starting states.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import traceback
import uuid
from experiments.scripts.validate_tb_ready_local import DATA, save, digest, classify_junit

TOOLS=DATA/'validation/tb-validation-tools-20261018'
UV_INSTALL=re.compile(r'curl -LsSf https://astral\.sh/uv/(?:(\d+\.\d+\.\d+)/)?install\.sh \| sh')
C4_HELDOUT_SHA='807a548efbb10153c9eff0df5733a97a1b51ab1743242530de1b02a8ea17ace7'
C4_HELDOUT_URL='https://hf-mirror.com/datasets/allenai/c4/resolve/1588ec454efa1a09f29cd18ddd04fe05fc8653a2/en/c4-train.00009-of-01024.json.gz'


def c4_local_test_transport(original):
    repo='"allenai/c4"';filename='"en/c4-train.00009-of-01024.json.gz"'
    assert original.count(repo)==1 and original.count(filename)==1
    return original.replace(repo,'"json"',1).replace(filename,
        '"/results/cache/verifier-assets/c4-heldout-00009.json.gz"',1)


C4_SETUP=r'''apt-get update
apt-get install -y curl
mkdir -p /results/cache/verifier-assets
curl --fail --location --connect-timeout 15 --max-time 600 --speed-time 90 --speed-limit 1024 --continue-at - --output /results/cache/verifier-assets/c4-heldout-00009.json.gz "'''+C4_HELDOUT_URL+r'''"
echo "'''+C4_HELDOUT_SHA+r'''  /results/cache/verifier-assets/c4-heldout-00009.json.gz" | sha256sum -c -
python3 - <<'C4_TRANSPORT_PY'
from pathlib import Path
import hashlib,json
p=Path('/tests/test_outputs.py');old=p.read_text()
assert old.count('"allenai/c4"')==1 and old.count('"en/c4-train.00009-of-01024.json.gz"')==1
new=old.replace('"allenai/c4"','"json"',1).replace('"en/c4-train.00009-of-01024.json.gz"','"/results/cache/verifier-assets/c4-heldout-00009.json.gz"',1)
p.write_text(new)
Path('/results/c4-verifier-transport.json').write_text(json.dumps(dict(original_test_sha256=hashlib.sha256(old.encode()).hexdigest(),effective_test_sha256=hashlib.sha256(new.encode()).hexdigest(),only_dataset_transport_changed=True,assertions_changed=False)))
C4_TRANSPORT_PY
'''

LC0_POSTCHECK=r'''/app/.tbench-testing/bin/python - <<'LC0_CHECK_PY'
import hashlib,json,subprocess
from pathlib import Path
import numpy,torch,torchvision,onnx,onnx2torch,onnxruntime
p=Path('/tmp/leela-network-gold.onnx')
assert p.is_file() and p.stat().st_size>1024,'Official reference ONNX generation failed'
onnx.checker.check_model(str(p))
session=onnxruntime.InferenceSession(str(p),providers=['CPUExecutionProvider'])
assert session.get_inputs() and session.get_outputs()
commit=subprocess.check_output(['git','-C','/tmp/lc0','rev-parse','HEAD'],text=True).strip()
Path('/results/lc0-verifier-prerequisites.json').write_text(json.dumps(dict(
    source_commit=commit,gold_onnx_sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
    gold_onnx_bytes=p.stat().st_size,official_reference_loads=True,
    numpy=numpy.__version__,torch=torch.__version__,torchvision=torchvision.__version__,
    onnx=onnx.__version__,onnxruntime=onnxruntime.__version__,model_calls=0)))
LC0_CHECK_PY
lc0_prereq_rc=$?
[ "$lc0_prereq_rc" -eq 0 ] || exit "$lc0_prereq_rc"
'''

LC0_NINJA_SETUP=r'''cat > /tmp/agenttx-ninja <<'LC0_NINJA_SH'
#!/bin/sh
exec /usr/bin/ninja -j4 "$@"
LC0_NINJA_SH
chmod +x /tmp/agenttx-ninja
export NINJA=/tmp/agenttx-ninja
"$NINJA" --version
'''


def effective_script(original, task_name=None, github_proxy=None, mobilesam_source=False, lc0_wheels=False, lc0_source=False, lc0_packages=False):
    versions=[]
    def replace(match):
        version=match.group(1) or '0.7.13'
        versions.append(dict(upstream=match.group(1) or 'unpinned',resolved=version))
        return ('mkdir -p "$HOME/.local/bin" && install -m755 /probe/uv-'+version+
                ' "$HOME/.local/bin/uv" && printf \'export PATH="$HOME/.local/bin:$PATH"\\n\' > "$HOME/.local/bin/env"')
    effective=UV_INSTALL.sub(replace,original)
    if task_name=='leelachess0-pytorch-conversion':
        assert effective.count('https://download.pytorch.org/whl/cpu')==1
        effective=effective.replace('https://download.pytorch.org/whl/cpu',
                                    'https://mirrors.nju.edu.cn/pytorch/whl/cpu',1)
        if lc0_wheels:
            from experiments.workloads.terminal_bench_suite import _LC0_CPU_WHEELS
            anchor='uv pip install torch==2.8.0 torchvision==0.23.0'
            assert effective.count(anchor)==1
            effective=effective.replace(anchor,'uv pip install '+
                ' '.join('/probe/cpu-wheels/'+name for name in _LC0_CPU_WHEELS),1)
        if lc0_source:
            anchor='git clone https://github.com/LeelaChessZero/lc0.git'
            assert effective.count(anchor)==1
            replacement='rm -rf /tmp/lc0 && cp -a --no-preserve=ownership /probe/lc0-source /tmp/lc0'
            if lc0_packages:
                replacement+=' && mkdir -p /tmp/lc0/subprojects/packagecache && cp -a --no-preserve=ownership /probe/lc0-packagecache/. /tmp/lc0/subprojects/packagecache/'
            effective=effective.replace(anchor,replacement,1)
    if task_name=='accelerate-maximal-square':
        # Upstream pins pytest 8 on Python 3.6, which cannot install it.
        # Keep the original interpreter/numpy and all test assertions; use
        # the last pytest release supporting Python 3.6 in the verifier only.
        assert effective.count('pip3 install pytest==8.4.1')==1
        effective=effective.replace('pip3 install pytest==8.4.1',
                                    'pip3 install pytest==6.2.5',1)
    if task_name in {'qemu-startup','qemu-alpine-ssh'}:
        assert 'apt-get install -y curl' in effective
        effective=effective.replace('apt-get install -y curl',
            'apt-get install -y python3-distutils curl',1)
    if task_name=='download-youtube':
        # Official verifier imports OpenCV but upstream forgot its shared libs.
        assert 'apt-get update' in effective
        effective=effective.replace('apt-get update','apt-get update\napt-get install -y libgl1 libglib2.0-0',1)
    if task_name=='sam-cell-seg' and mobilesam_source:
        requirement='"git+https://github.com/ChaoningZhang/MobileSAM.git@${COMMIT_HASH}"'
        assert effective.count(requirement)==1
        effective=effective.replace(requirement,'"/var/tmp/agenttx-mobilesam-source"')
    if task_name=='sam-cell-seg' and github_proxy:
        url='https://github.com/ChaoningZhang/MobileSAM/raw/master/weights/mobile_sam.pt'
        assert url in effective
        effective=effective.replace(url,github_proxy+url)
        effective=effective.replace('# Copy test data',
            'sha256sum /app/mobile_sam.pt > /results/mobilesam-weight.sha256\n\n# Copy test data',1)
    return effective,versions


SETUP=r'''#!/bin/bash
set -eu
export DEBIAN_FRONTEND=noninteractive
export PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
export UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple
unset UV_DEFAULT_INDEX
export UV_CACHE_DIR=/results/cache/uv
export PIP_CACHE_DIR=/results/cache/pip
export UV_PYTHON_DOWNLOADS=never
export UV_HTTP_TIMEOUT=40
export PIP_DEFAULT_TIMEOUT=40
export PIP_RETRIES=1
export TEST_DIR=/tests
export PYTEST_ADDOPTS=--junitxml=/results/baseline.xml
unset PYTHONPATH
mkdir -p /tests
cp -a /probe/tests/. /tests/
for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do
 [ -f "$source" ] || continue
 sed -i -E -e 's|https?://archive.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
 -e 's|https?://security.ubuntu.com/ubuntu|https://mirrors.tuna.tsinghua.edu.cn/ubuntu|g' \
 -e 's|https?://deb.debian.org/debian|https://mirrors.tuna.tsinghua.edu.cn/debian|g' \
 -e 's|https?://security.debian.org/debian-security|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' "$source"
done
printf 'Acquire::Retries "1"; Acquire::http::Timeout "15"; Acquire::https::Timeout "15";\n' > /etc/apt/apt.conf.d/99agenttx-validation
set +e
bash -x /probe/effective-run-tests.sh > /results/baseline.stdout 2> /results/baseline.stderr
rc=$?
printf '%s\n' "$rc" > /results/baseline.rc
exit "$rc"
'''


def run(record,output,timeout,python_index="https://pypi.tuna.tsinghua.edu.cn/simple",github_proxy=None,mobilesam_source=None,download_timeout=40):
    from experiments.workloads import terminal_bench_suite as tb
    folder=output/record['task'];folder.mkdir()
    source=tb.task_source(DATA/'cache',tb.load_tasks(DATA/'cache')[record['task']])
    original=(source/'run-tests.sh').read_text()
    cpu_wheels={}
    lc0_source=None
    lc0_packages=None
    if record['task']=='leelachess0-pytorch-conversion':
        cache=tb._asset_cache_dir()
        if all((cache/name).is_file() for name in tb._LC0_CPU_WHEELS):
            for name,expected in tb._LC0_CPU_WHEELS.items():
                tb._verify_local_asset(cache/name,expected);cpu_wheels[name]=expected
        if (cache/'lc0-verifier-source-proof.json').is_file():
            from experiments.scripts.repair_tb_lc0_verifier_source import COMMIT,TREE
            lc0_source=cache/'lc0-verifier-source'
            proof=json.loads((cache/'lc0-verifier-source-proof.json').read_text())
            assert proof['commit']==COMMIT and proof['tree']==TREE and proof['exact_signed_commit_reproduced']
            assert subprocess.check_output(['git','-C',str(lc0_source),'rev-parse','HEAD'],text=True).strip()==COMMIT
            assert not subprocess.check_output(['git','-C',str(lc0_source),'status','--porcelain','--ignored'],text=True).strip()
        if lc0_source and (cache/'lc0-verifier-packagecache-proof.json').exists():
            lc0_packages=json.loads((cache/'lc0-verifier-packagecache-proof.json').read_text())
            for asset in lc0_packages['assets']:
                tb._verify_local_asset(cache/'lc0-verifier-packagecache'/asset['file'],asset['sha256'])
    effective,versions=effective_script(original,record['task'],github_proxy,mobilesam_source is not None,bool(cpu_wheels),lc0_source is not None,lc0_packages is not None)
    (folder/'effective-run-tests.sh').write_text(effective)
    setup=SETUP.replace('https://pypi.tuna.tsinghua.edu.cn/simple',python_index)
    if record['task']=='reshard-c4-data':
        setup=setup.replace('set +e\n',C4_SETUP+
            'export HF_ENDPOINT=https://hf-mirror.com\nexport HF_HOME=/results/cache/huggingface\nset +e\n',1)
    if record['task']=='leelachess0-pytorch-conversion':
        setup=setup.replace('set +e\n',LC0_NINJA_SETUP+'set +e\n',1)
        setup=setup.replace('rc=$?\n','rc=$?\n'+LC0_POSTCHECK,1)
    assert 40<=download_timeout<=600
    setup=setup.replace('export UV_HTTP_TIMEOUT=40','export UV_HTTP_TIMEOUT='+str(download_timeout))
    setup=setup.replace('export PIP_DEFAULT_TIMEOUT=40','export PIP_DEFAULT_TIMEOUT='+str(download_timeout))
    if mobilesam_source is not None:
        setup=setup.replace('set +e\n','cp -a /probe/mobilesam-source /var/tmp/agenttx-mobilesam-source\nset +e\n',1)
    if github_proxy:
        # Git still verifies and checks out the exact upstream requested commit.
        prefix='if command -v git >/dev/null; then git config --global url."'+github_proxy+'https://github.com/".insteadOf "https://github.com/"; fi\n'
        setup=setup.replace('set +e\n',prefix+'set +e\n',1)
    (folder/'run.sh').write_text(setup)
    tests=source/'tests'
    if not tests.exists():
        tests=folder/'empty-tests';tests.mkdir()
    row=dict(task=record['task'],started_at=datetime.now(timezone.utc).isoformat(),
             image_id=record['local_image_id'],model_calls=0,gold_solution_used=False,python_index=python_index,github_proxy=github_proxy,download_timeout_s=download_timeout,
             verifier_user='root',agent_user_unmodified=True,
             original_script_sha256=digest(source/'run-tests.sh'),
             effective_script_sha256=digest(folder/'effective-run-tests.sh'),uv_resolution=versions,
             test_assets={str(p.relative_to(tests)):digest(p) for p in tests.rglob('*') if p.is_file()},
             immutable_original_workspace=True,prepared_image_committed=False,
             added_system_prerequisites=(['libgl1','libglib2.0-0'] if record['task']=='download-youtube' else
                 ['python3-distutils'] if record['task'] in {'qemu-startup','qemu-alpine-ssh'} else []),
             transport_changes=['Local uv binary with recorded version/hash replaces installer download.',
                                'APT and default Python indexes use TUNA; explicit upstream indexes preserved.'])
    name='agenttx-tb-bootstrap-'+uuid.uuid4().hex
    cpus,memory=('4','12g') if record['task'] in {'predicate-pushdown-bench','leelachess0-pytorch-conversion'} else ('1','4g')
    row['diagnostic_resources']=dict(cpus=cpus,memory=memory)
    if record['task']=='accelerate-maximal-square':
        row['verifier_compatibility_exception']=dict(
            original_pytest='8.4.1',effective_pytest='6.2.5',
            reason='Original Python 3.6 cannot run pytest 8; 6.2.5 is its last supported pytest release.',
            task_interpreter_unmodified=True,test_assertions_unmodified=True,
            original_verifier_pin_preserved=False,created_only_during_verification=True)
    if record['task']=='leelachess0-pytorch-conversion':
        row['cpu_wheel_transport']=dict(index='https://mirrors.nju.edu.cn/pytorch/whl/cpu',
            torch='2.8.0+cpu',torchvision='0.23.0+cpu',
            mirror_identity_proof=str(DATA/'validation/tb-lc0-cpu-mirror-proof-20261020-r1/proof.json'))
    command=['docker','run','--rm','--pull=never','--name',name,
             '--label','agenttx.tb-bootstrap='+output.name,'--network=host','--cpus='+cpus,'--memory='+memory,
             '--user','0','--env','HOME=/root',
             '--mount',f'type=bind,src={folder},dst=/results',
             '--mount',f'type=bind,src={tests},dst=/probe/tests,readonly',
             '--mount',f'type=bind,src={folder}/effective-run-tests.sh,dst=/probe/effective-run-tests.sh,readonly',
             '--mount',f'type=bind,src={folder}/run.sh,dst=/probe/run.sh,readonly']
    row['uv_binaries']={}
    if cpu_wheels:
        row['cpu_wheel_assets']=cpu_wheels
        for filename in cpu_wheels:
            command+=['--mount',f'type=bind,src={tb._asset_cache_dir()/filename},dst=/probe/cpu-wheels/{filename},readonly']
    if lc0_source is not None:
        command+=['--mount',f'type=bind,src={lc0_source},dst=/probe/lc0-source,readonly']
        row['verifier_tool_source']=dict(commit=COMMIT,tree=TREE,
            proof_sha256=digest(tb._asset_cache_dir()/'lc0-verifier-source-proof.json'),
            compiler_parallel_jobs=4,original_unpinned_source_now_frozen=True)
    if lc0_packages is not None:
        command+=['--mount',f'type=bind,src={tb._asset_cache_dir()/"lc0-verifier-packagecache"},dst=/probe/lc0-packagecache,readonly']
        row['verifier_packagecache']=lc0_packages
    if record['task']=='reshard-c4-data':
        row['verifier_dataset_transport']=dict(url=C4_HELDOUT_URL,sha256=C4_HELDOUT_SHA,
            created_only_during_verification=True,agent_starting_image_unmodified=True,
            original_test_sha256=digest(tests/'test_outputs.py'),
            effective_test_sha256=hashlib.sha256(c4_local_test_transport((tests/'test_outputs.py').read_text()).encode()).hexdigest(),
            assertions_changed=False)
    if mobilesam_source is not None:
        assert record['task']=='sam-cell-seg'
        from experiments.scripts.repair_tb_mobilesam_transport import verify_source, COMMIT
        tree=json.loads((mobilesam_source.parent/'github-tree.json').read_text())
        row['verified_source']=dict(commit=COMMIT,blobs=verify_source(mobilesam_source,tree),
            proof_sha256=digest(mobilesam_source.parent/'source-proof.json'))
        command+=['--mount',f'type=bind,src={mobilesam_source},dst=/probe/mobilesam-source,readonly']
    for version in sorted({v['resolved'] for v in versions}):
        binary=TOOLS/'bin/uv' if version=='0.7.13' else TOOLS/f'uv-{version}/bin/uv'
        row['uv_binaries'][version]=digest(binary)
        command+=['--mount',f'type=bind,src={binary},dst=/probe/uv-{version},readonly']
    command+=['--entrypoint','bash',record['local_image_id'],'/probe/run.sh']
    from experiments.scripts.tb_task_services import TaskServices
    services=TaskServices(source,record['task'],folder,record['local_image_id']).start()
    try:command=services.apply(command)
    except Exception:
        services.close();raise
    if services.evidence:
        row['sidecar_evidence']=str(services.evidence)
        row['sidecar_network']=services.network
        row['fresh_sidecar_volumes']=True
    save(folder/'command.json',command);save(folder/'registration.json',row)
    start=time.monotonic();rc=None
    try:
        with (folder/'container.stdout').open('w') as out,(folder/'container.stderr').open('w') as err:
            rc=subprocess.run(command,stdout=out,stderr=err,timeout=timeout).returncode
    except subprocess.TimeoutExpired:
        rc=124
    except Exception as exc:
        row['exception']=repr(exc);(folder/'exception.txt').write_text(traceback.format_exc())
    finally:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=30)
        services.close()
    row.update(container_returncode=rc,wall_s=round(time.monotonic()-start,2))
    rcfile=folder/'baseline.rc'
    row['baseline_returncode']=int(rcfile.read_text()) if rcfile.exists() else rc
    row['baseline_classification'],row['baseline_tests']=classify_junit(folder/'baseline.xml',124 if rc==124 else row['baseline_returncode'])
    row['official_tests_executed']=row['baseline_classification'] in {'baseline_failed','baseline_passed'}
    # This is baseline-only validation, never a claim of solution correctness.
    row['full_positive_validation']=False
    if (folder/'lc0-verifier-prerequisites.json').exists():
        row['verifier_prerequisites']=json.loads((folder/'lc0-verifier-prerequisites.json').read_text())
        row['verifier_prerequisites_sha256']=digest(folder/'lc0-verifier-prerequisites.json')
    row['finished_at']=datetime.now(timezone.utc).isoformat()
    save(folder/'result.json',row)
    print(json.dumps({k:row[k] for k in ['task','baseline_classification','wall_s']}),flush=True)
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--workers',type=int,default=4)
    parser.add_argument('--timeout',type=int,default=900)
    parser.add_argument('--download-timeout',type=int,default=40)
    parser.add_argument('--tasks',nargs='+')
    parser.add_argument('--python-index',default='https://pypi.tuna.tsinghua.edu.cn/simple',
        choices=['https://pypi.tuna.tsinghua.edu.cn/simple','https://mirrors.aliyun.com/pypi/simple'])
    parser.add_argument('--github-proxy',choices=['https://gh-proxy.com/','https://ghfast.top/'])
    args=parser.parse_args()
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    args.output.mkdir(exist_ok=False)
    summary=json.loads((DATA/'validation/tb-ready-validation-summary-20261018.json').read_text())
    selected=args.tasks or [r['task'] for r in summary['checks'] if r['classification']=='test_environment_needs_work']
    evidence=json.loads((DATA/'validation/tb-readiness-recheck-20261018.json').read_text())
    records=[r for r in evidence['checks'] if r['task'] in selected and r['identity_verified']]
    # Newly repaired images are absent from the older diagnostic inventory.
    # Resolve only the content-derived official tag, then pin its local ID.
    from experiments.workloads import terminal_bench_suite as tb
    tasks=tb.load_tasks(DATA/'cache')
    for name in sorted(set(selected)-{r['task'] for r in records}):
        task=tasks[name];source=tb.task_source(DATA/'cache',task)
        tag=tb._official_image_tag(task,source)
        info=json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True,timeout=30))[0]
        records.append(dict(task=name,local_image_id=info['Id'],identity_verified=True,
            image_tag=tag,original_dockerfile_sha256=digest(tb._docker_build_paths(source)[1])))
    assert len(records)==len(selected)
    save(args.output/'registration.json',dict(tasks=selected,workers=args.workers,timeout_s=args.timeout,
        model_calls=0,gold_solution_used=False,source_sha256=digest(Path(__file__)),
        purpose='Actual original verifier bootstrap, baseline only; no workspace/image reused by agents.'))
    rows=[]
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs={pool.submit(run,r,args.output,args.timeout,args.python_index,args.github_proxy,download_timeout=args.download_timeout):r['task'] for r in records}
        for job in as_completed(jobs):
            try:row=job.result()
            except Exception as exc:
                folder=args.output/jobs[job];folder.mkdir(exist_ok=True)
                (folder/'coordinator-error.txt').write_text(traceback.format_exc())
                row=dict(task=jobs[job],official_tests_executed=False,model_calls=0,
                    baseline_classification='environment_preparation_error',error=repr(exc))
                save(folder/'result.json',row)
            rows.append(row);save(args.output/'progress.json',dict(completed=len(rows),total=len(records),tasks=rows))
    save(args.output/'execution.json',dict(finished_at=datetime.now(timezone.utc).isoformat(),
        attempted=len(rows),official_tests_executed=sum(r['official_tests_executed'] for r in rows),tasks=rows))


if __name__=='__main__':main()
