#!/usr/bin/env python3
"""Prepare and monitor task images/assets on the local data disk; no model calls."""
from concurrent.futures import ThreadPoolExecutor
from collections import Counter
from itertools import zip_longest
import csv
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from experiments.scripts.experiment_storage import experiment_path
from experiments.scripts.docker_pull_progress import pull_image, IDLE_TIMEOUT_S, WALL_TIMEOUT_S

DATA = Path('/mnt/data/agenttx-runtime')
COHORT = DATA / 'validation' / os.environ.get('AGENTTX_ENVIRONMENT_COHORT', 'environment-preparation-local-20261017-r3')
MIRRORS = ['docker.1ms.run', 'hub.rat.dev', 'docker.1panel.live']
PREVIOUS = DATA / 'validation/environment-preparation-local-20261017-r2'
COMMIT = 'd28711d0da2675d0bb1d56de45ae5df6082438a3'


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    tmp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    tmp.write_text(json.dumps(value, indent=2) + '\n')
    tmp.replace(path)


def download(url, destination):
    started = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    partial = destination.with_suffix(destination.suffix + '.part')
    with opener.open(url, timeout=30) as response, partial.open('wb') as stream:
        headers = dict(response.headers)
        while chunk := response.read(1024 * 1024):
            stream.write(chunk)
            if time.monotonic() - started > 1200:
                raise TimeoutError('Registered source-download wall time exceeded')
            if stream.tell() > 2 * 1024**3:
                raise ValueError('Source archive exceeds 2 GiB')
    partial.replace(destination)
    return dict(url=url, bytes=destination.stat().st_size,
                sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                revision=headers.get('X-Repo-Commit') or headers.get('x-repo-commit'))


def hydrate_swe(cache, scope):
    import pyarrow.parquet as pq
    archive = DATA / 'downloads/swe-bench-lite-test.parquet'
    if archive.exists() and (PREVIOUS/'swe-metadata.json').exists():
        record = json.loads((PREVIOUS/'swe-metadata.json').read_text())['download']
        assert archive.stat().st_size == record['bytes']
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == record['sha256']
        record = dict(record, reused_from=str(PREVIOUS))
    else:
        record = download('https://hf-mirror.com/datasets/princeton-nlp/SWE-bench_Lite/resolve/main/data/test-00000-of-00001.parquet?download=true', archive)
    rows = pq.read_table(archive).to_pylist()
    assert len(rows) == 300 and {r['instance_id'] for r in rows} == set(scope)
    previous = json.loads((experiment_path('results') / 'environment_preparation_20261017/environment-preparation-20261017-r2/registration.json').read_text())
    bases = {r['task']: r['base_commit'] for r in previous['tasks']}
    dest = cache / 'swe_bench'
    dest.mkdir(parents=True, exist_ok=True)
    for row in rows:
        assert row['base_commit'] == bases[row['instance_id']]
        target = dest / (row['instance_id'] + '.json')
        if target.exists():
            assert json.loads(target.read_text()) == row
        else:
            save(target, row)
    manifest = dest / 'lite_test_manifest.json'
    if manifest.exists():
        assert {r['instance_id']: r for r in json.loads(manifest.read_text())} == {r['instance_id']: r for r in rows}
    else:
        save(manifest, sorted(rows, key=lambda r: r['instance_id']))
    save(COHORT / 'swe-metadata.json', dict(status='verified', tasks=300, download=record,
                                          pinned_base_commits_match=True, host=socket.gethostname()))
    return {r['instance_id']: r for r in rows}


def hydrate_tb(cache, source_manifest):
    expected = {row['path']: row for row in source_manifest['files']}
    assert source_manifest['commit'] == COMMIT and len(expected) == 1818
    archive = DATA / 'downloads/terminal-bench-pinned.tar.gz'
    output = cache / 'terminal_bench' / ('terminal-bench-1-' + COMMIT[:12])
    active = cache / 'terminal_bench/terminal-bench-1'
    if active.is_symlink() and output.exists():
        assert active.resolve() == output.resolve()
        previous = json.loads((PREVIOUS/'tb-source.json').read_text())
        assert previous['commit'] == COMMIT and previous['status'] == 'task_sources_hydrated'
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == previous['download']['sha256']
        for relative, want in expected.items():
            dest = output / relative
            assert dest.resolve().is_relative_to(output.resolve()), relative
            raw = os.readlink(dest).encode() if dest.is_symlink() else dest.read_bytes()
            assert hashlib.sha256(raw).hexdigest() == want['sha256'], relative
            assert hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest() == want['git_blob'], relative
            if want['git_mode'] == '120000':
                assert dest.is_symlink(), relative
            else:
                assert not dest.is_symlink(), relative
                assert bool(dest.stat().st_mode & 0o111) == (want['git_mode']=='100755'), relative
        save(COHORT/'tb-source.json', dict(previous, checked_at=now(), reused_from=str(PREVIOUS),
                                         files_verified=len(expected), all_files_reverified=True))
        return
    url = f'https://gh-proxy.com/https://github.com/harbor-framework/terminal-bench-1/archive/{COMMIT}.tar.gz'
    record = download(url, archive)
    output = cache / 'terminal_bench' / ('terminal-bench-1-' + COMMIT[:12])
    output.mkdir(parents=True, exist_ok=False)
    checked = []
    with tarfile.open(archive, 'r:gz') as tar:
        for member in tar:
            parts = Path(member.name).parts
            relative = str(Path(*parts[1:])) if len(parts) > 1 else ''
            if relative not in expected:
                continue
            want = expected[relative]
            dest = output / relative
            assert dest.resolve().is_relative_to(output.resolve())
            dest.parent.mkdir(parents=True, exist_ok=True)
            if member.issym():
                raw = member.linkname.encode()
                assert not Path(member.linkname).is_absolute()
                assert (dest.parent / member.linkname).resolve().is_relative_to(output.resolve())
                dest.symlink_to(member.linkname)
            else:
                assert member.isfile() and member.size <= 1024**3
                with tar.extractfile(member) as stream:
                    raw = stream.read()
                dest.write_bytes(raw)
                dest.chmod(0o755 if want['git_mode'] == '100755' else 0o644)
            assert hashlib.sha256(raw).hexdigest() == want['sha256'], relative
            assert hashlib.sha1(b'blob ' + str(len(raw)).encode() + b'\0' + raw).hexdigest() == want['git_blob'], relative
            checked.append(relative)
    assert set(checked) == set(expected)
    helpers = cache / 'terminal_bench/terminal-bench-1/terminal_bench'
    if helpers.exists():
        shutil.copytree(helpers, output / 'terminal_bench')
    # Preserve the recovered incomplete task tree as historical evidence.
    active = cache / 'terminal_bench/terminal-bench-1'
    assert not active.is_symlink()
    retired = active.with_name('terminal-bench-1.recovered-incomplete')
    assert not retired.exists()
    if active.exists():
        active.rename(retired)
    active.symlink_to(output, target_is_directory=True)
    save(COHORT / 'tb-source.json', dict(status='task_sources_hydrated', host=socket.gethostname(),
         checked_at=now(), commit=COMMIT, files_verified=len(checked), cache=str(output),
         download=record, agent_solution_files_excluded=True))


def main():
    assert os.environ.get('AGENTTX_LOCAL_ONLY') == '1'
    assert socket.gethostname() == 'r760-ssd'
    cache = experiment_path('cache')
    results = experiment_path('results')
    assert os.environ.get('DOCKER_HOST') == 'unix:///run/agenttx-docker/docker.sock'
    info = json.loads(subprocess.check_output(['docker', 'info', '--format', '{{json .}}'], text=True))
    assert info['Name'] == 'r760-ssd' and info['DockerRootDir'] == str(DATA / 'docker')
    assert not info.get('HTTPProxy') and not info.get('HTTPSProxy')
    assert info['Containerd']['Address'] == '/run/agenttx-containerd/containerd.sock'
    assert info['Containerd']['Namespaces']['Containers'] == 'agenttx-moby'
    lock = (DATA / 'validation/local-environment.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    COHORT.mkdir(exist_ok=False)
    save(COHORT / 'progress.json', dict(updated_at=now(), stage='waiting_for_local_data_migration',
         host=socket.gethostname(), coordinator_pid=os.getpid(), model_experiments_started=0, tasks=[]))
    wait_started = time.monotonic()
    while not all((ROOT / 'experiments' / kind).is_symlink() for kind in ('cache', 'logs', 'results')):
        if time.monotonic() - wait_started > 3600:
            raise TimeoutError('Data migration has not completed within one hour')
        time.sleep(5)
    with (ROOT / 'experiments/plans/recovery_20261017/official_cells.csv').open() as stream:
        scope = sorted({row['task'] for row in csv.DictReader(stream) if row['suite'] == 'swe'})
    source_manifest = json.loads((results / 'environment_preparation_20261017/tb-source-file-manifest-20261017.json').read_text())
    shutil.copy2(ROOT / 'experiments/scripts/swe_image_content_probe.py', COHORT / 'content-probe.py')
    shutil.copy2(Path(__file__), COHORT / 'preparer.py')
    shutil.copy2(ROOT/'experiments/scripts/docker_pull_progress.py', COHORT/'docker_pull_progress.py')
    save(COHORT / 'progress.json', dict(updated_at=now(), stage='hydrating_local_metadata',
         host=socket.gethostname(), coordinator_pid=os.getpid(), model_experiments_started=0, tasks=[]))
    metadata = hydrate_swe(cache, scope)
    prior_progress = json.loads((PREVIOUS/'progress.json').read_text())
    prior_rows = {r['task']: r for r in prior_progress['tasks']}
    prior_counts = dict(Counter(r['state'] for r in prior_rows.values()))
    rows = {task: dict(task=task, state='pending', base_commit=metadata[task]['base_commit'],
                      previous_state=prior_rows.get(task,{}).get('state'),
                      previous_evidence=str(PREVIOUS/task)) for task in scope}
    cached_scope = [t for t in scope if prior_rows.get(t,{}).get('state')=='image_ready']
    families = {}
    for task in scope:
        if task not in cached_scope:
            families.setdefault(task.split('__')[0], []).append(task)
    # Recheck all cached identities first; then interleave remaining repositories.
    queue = cached_scope + [t for group in zip_longest(*families.values()) for t in group if t]
    assert len(queue)==len(scope) and set(queue)==set(scope)
    save(COHORT / 'registration.json', dict(registered_at=now(), host=socket.gethostname(),
        docker_host=os.environ['DOCKER_HOST'], docker_root=info['DockerRootDir'], containerd=info['Containerd'], cache=str(cache),
        image_workers=2, mirrors=MIRRORS, mirror_idle_timeout_s=IDLE_TIMEOUT_S,
        mirror_wall_timeout_s=WALL_TIMEOUT_S, progress_source='Docker API layer byte counters and completed-layer milestones',
        min_free_gib=150, previous_environment_cohort=str(PREVIOUS),
        previous_state_counts=prior_counts, task_order=queue,
        previous_progress_sha256=hashlib.sha256((PREVIOUS/'progress.json').read_bytes()).hexdigest(),
        pull_helper_sha256=hashlib.sha256((COHORT/'docker_pull_progress.py').read_bytes()).hexdigest(),
        automatic_model_launch=False, formal_eligible=False,
        tasks=[dict(task=t, base_commit=metadata[t]['base_commit'], metadata_sha256=hashlib.sha256((cache/'swe_bench'/(t+'.json')).read_bytes()).hexdigest()) for t in scope],
        preparer_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        content_probe_sha256=hashlib.sha256((COHORT/'content-probe.py').read_bytes()).hexdigest()))
    mutex = threading.Lock()
    stop_monitor = threading.Event()
    env = os.environ.copy()
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'http_proxy', 'https_proxy', 'all_proxy'):
        env.pop(key, None)

    def update(task, **event):
        with mutex:
            rows[task].update(event)
            with (COHORT / 'events.jsonl').open('a') as stream:
                stream.write(json.dumps(dict(task=task, at=now(), **event))+'\n')
            save(COHORT / 'progress.json', dict(updated_at=now(), stage='preparing_local_images',
                 host=socket.gethostname(), coordinator_pid=os.getpid(), model_experiments_started=0, tasks=list(rows.values())))

    def monitor():
        while not stop_monitor.is_set():
            with mutex:
                counts = dict(Counter(r['state'] for r in rows.values()))
                errors = [dict(task=r['task'], error=r.get('error')) for r in rows.values() if r['state']=='environment_error']
                active = [dict(task=r['task'], started_at=r.get('started_at')) for r in rows.values() if r['state']=='preparing']
            tb_file = COHORT / 'tb-source.json'
            tb = json.loads(tb_file.read_text()) if tb_file.exists() else {}
            summary = dict(checked_at=now(), host=socket.gethostname(), code_root=str(ROOT),
                docker_host=os.environ['DOCKER_HOST'], docker_root=info['DockerRootDir'],
                cache=str(cache), results=str(results), image_stage_counts=counts,
                active_tasks=active, image_errors=errors, disk_free_gib=round(shutil.disk_usage(DATA).free/2**30, 1),
                tb_source=tb, automatic_model_launch=False, model_experiments_started=0,
                previous_environment_cohort=str(PREVIOUS), previous_state_counts=prior_counts,
                full_task_environments_ready=False, image_stage_finished=(COHORT/'execution.json').exists())
            save(COHORT/'summary.json', summary)
            with (COHORT/'monitor-observations.jsonl').open('a') as stream:
                stream.write(json.dumps(summary)+'\n')
            checked = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
            (ROOT/'docs/DOCKER_ENVIRONMENT_20261017.md').write_text('\n'.join([
                '# 本机 Docker 与实验环境准备', '', f'核对时间：{checked}（北京时间）。', '',
                '**执行位置已改正：代码修改、准备和核验均在本机 `r760-ssd`。远程环境队列已停止，远程仅作为参考代码来源。此前远程 61 个就绪镜像和 TB 核验不计作本机进度。**', '',
                '代码目录：`'+str(ROOT)+'`。Docker、缓存、结果、日志和临时文件位于 `/mnt/data/agenttx-runtime`；项目原有数据目录迁移并校验后保留软链接。', '',
                '本机专用 Docker：`'+os.environ['DOCKER_HOST']+'`；数据根目录：`'+info['DockerRootDir']+'`。使用国内镜像源，无 daemon 代理；原有共享 Docker 和其他容器未重启。独立 daemon 禁用 bridge/iptables，需联网的构建和 native agent 显式使用 host 网络，验收可使用 none 网络。', '',
                '当前镜像阶段：`'+json.dumps(counts, ensure_ascii=False)+'`。最多 2 个环境 worker；按 Docker API 下载/解压字节与完成层判断进展，连续 900 秒无进展或单源总时长 14400 秒才中断换源；连接错误立即换源。保留此前失败记录，不直接回退 Docker Hub。候选源：'+', '.join(MIRRORS)+'。', '',
                '当前 TB 源码状态：`'+str(tb.get('status','preparing'))+'`；已验证文件：'+str(tb.get('files_verified',0))+'。', '',
                '此前 r2 状态：`'+json.dumps(prior_counts)+'`，完整记录保存在 `'+str(PREVIOUS)+'`；本批重新核验缓存并按仓库交错准备剩余镜像。', '',
                '可用数据盘：'+str(summary['disk_free_gib'])+' GiB。当前错误：`'+json.dumps(errors,ensure_ascii=False)+'`。', '',
                '**镜像就绪不等于任务环境就绪。** 干净 base-commit 源码、依赖、官方验收及协议门槛仍需完成；模型实验保持暂停。', '',
                '本机监控每 60 秒更新。完整证据和进度：`'+str(COHORT)+'`。', '']))
            inventory_path = ROOT/'docs/EXPERIMENT_STATUS_20261017.json'
            inventory = json.loads(inventory_path.read_text())
            inventory['environment_preparation'] = summary
            save(inventory_path, inventory)
            if stop_monitor.wait(60):
                break

    def prepare(task):
        if shutil.disk_usage(DATA).free < 150*2**30:
            update(task, state='deferred_disk_guard')
            return
        update(task, state='preparing', started_at=now())
        logs = COHORT / task
        logs.mkdir()
        tag = 'swebench/sweb.eval.x86_64.' + task.replace('__','_1776_').lower() + ':latest'
        attempts = []
        try:
            inspected = subprocess.run(['docker','image','inspect',tag],capture_output=True,text=True,timeout=30)
            cached = inspected.returncode == 0
            if not cached:
                for mirror in MIRRORS:
                    def observe(progress):
                        save(logs/'pull-progress.json', dict(progress, mirror=mirror, observed_at=now()))
                    result = pull_image(mirror+'/'+tag, logs/(mirror+'.log'), env=env, observe=observe)
                    attempts.append(dict(mirror=mirror, **result))
                    update(task, pull_attempts=attempts)
                    if result['returncode'] == 0:
                        subprocess.run(['docker','tag',mirror+'/'+tag,tag],check=True,timeout=30)
                        break
                else:
                    raise RuntimeError('Domestic image sources failed; no direct upstream fallback')
            image = json.loads(subprocess.check_output(['docker','image','inspect',tag],text=True,timeout=30))[0]
            assert image['Architecture']=='amd64' and image['Os']=='linux'
            probe = subprocess.run(['docker','run','--rm','--pull=never','--network=none','--entrypoint','/opt/miniconda3/envs/testbed/bin/python',image['Id'],'-c',(COHORT/'content-probe.py').read_text(),metadata[task]['base_commit']],env=env,capture_output=True,text=True,timeout=90)
            (logs/'runtime.stdout').write_text(probe.stdout)
            (logs/'runtime.stderr').write_text(probe.stderr)
            assert probe.returncode == 0, probe.stderr[-500:]
            content = json.loads(probe.stdout)
            assert content['base_is_ancestor'] and content['working_tree_tracked_content_clean']
            update(task, state='image_ready', finished_at=now(), image_tag=tag, image_id=image['Id'],
                   repo_digests=image.get('RepoDigests',[]), content_probe=content, previously_cached=cached, pull_attempts=attempts)
        except Exception as exc:
            update(task,state='environment_error',finished_at=now(),error=f'{type(exc).__name__}: {exc}',pull_attempts=attempts)

    watcher = threading.Thread(target=monitor, daemon=True)
    watcher.start()
    def tb_job():
        try:
            hydrate_tb(cache, source_manifest)
        except Exception as exc:
            save(COHORT/'tb-source.json',dict(status='environment_error',error=f'{type(exc).__name__}: {exc}',checked_at=now()))
    with ThreadPoolExecutor(max_workers=3) as pool:
        tb_future = pool.submit(tb_job)
        # Two image workers; TB source I/O is the third environment job.
        with ThreadPoolExecutor(max_workers=2) as images:
            list(images.map(prepare, queue))
        tb_future.result()
    save(COHORT/'execution.json',dict(finished_at=now(),host=socket.gethostname(),tasks=list(rows.values()),model_experiments_started=0))
    stop_monitor.set()
    watcher.join(timeout=10)
    # One final snapshot; do not launch models at the end of this stage.
    stop_monitor.clear()
    final = threading.Thread(target=monitor,daemon=True)
    final.start()
    time.sleep(1)
    stop_monitor.set()
    final.join(timeout=10)


if __name__ == '__main__':
    main()
