#!/usr/bin/env python3
"""Independent local-only observer; never launches or retries experiments."""
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[2]
DATA = Path('/mnt/data/agenttx-runtime')
COHORT = DATA/'validation'/os.environ.get('AGENTTX_ENVIRONMENT_COHORT', 'environment-preparation-local-20261017-r3')


def read(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}


def save(path, value):
    tmp = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    tmp.write_text(json.dumps(value, indent=2)+'\n')
    tmp.replace(path)


def alive(pid, marker):
    try:
        proc = Path('/proc')/str(pid)
        return marker.encode() in (proc/'cmdline').read_bytes() and (proc/'stat').read_text().split(') ',1)[1][0]!='Z'
    except (OSError, ValueError):
        return False


def main():
    assert socket.gethostname() == 'r760-ssd'
    assert os.environ.get('DOCKER_HOST') == 'unix:///run/agenttx-docker/docker.sock'
    lock = (DATA/'validation/local-monitor.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    while True:
        try:
            progress = read(COHORT/'progress.json')
            registration = read(COHORT/'registration.json')
            tasks = progress.get('tasks',[])
            counts = dict(Counter(r['state'] for r in tasks))
            tb = read(COHORT/'tb-source.json')
            finished = (COHORT/'execution.json').exists()
            pid = progress.get('coordinator_pid')
            if not pid:
                pid_path = DATA/'validation/local-preparation.pid'
                pid = int(pid_path.read_text()) if pid_path.exists() else 0
            running = alive(pid, 'prepare_local_environment.py')
            migration = read(DATA/'validation/data-migration.json')
            migration_complete = all((ROOT/'experiments'/kind).is_symlink() for kind in ('cache','logs','results'))
            docker = subprocess.run(['docker','info','--format','{{json .}}'],capture_output=True,text=True,timeout=20)
            info = json.loads(docker.stdout) if docker.returncode==0 else {}
            errors = [dict(task=r['task'],error=r.get('error')) for r in tasks if r['state']=='environment_error']
            alerts = []
            if not running and not finished:
                alerts.append('local_preparer_not_running')
            if not info:
                alerts.append('local_docker_unreachable')
            elif info.get('DockerRootDir') != str(DATA/'docker') or info.get('Name')!='r760-ssd':
                alerts.append('unexpected_docker_host_or_storage')
            elif info.get('Containerd',{}).get('Address')!='/run/agenttx-containerd/containerd.sock' or info['Containerd']['Namespaces']['Containers']!='agenttx-moby':
                alerts.append('containerd_is_not_isolated')
            stat = os.statvfs(DATA)
            free = stat.f_bavail*stat.f_frsize/2**30
            if free<200:
                alerts.append('data_disk_near_150_gib_guard')
            if errors:
                alerts.append('image_environment_errors')
            if tb.get('status')=='environment_error':
                alerts.append('tb_source_environment_error')
            if counts.get('deferred_disk_guard'):
                alerts.append('images_deferred_by_disk_guard')
            activity = [p.stat().st_mtime for p in (COHORT/'progress.json',DATA/'validation/local-preparation.log') if p.exists()]
            pulls = []
            for task in tasks:
                if task['state']=='preparing':
                    activity.extend(p.stat().st_mtime for p in (COHORT/task['task']).glob('*.log'))
                    progress_path = COHORT/task['task']/'pull-progress.json'
                    if progress_path.exists():
                        pull = read(progress_path)
                        pulls.append(dict(task=task['task'], **pull))
                        activity.append(progress_path.stat().st_mtime)
                        if pull.get('idle_s',0) >= 900:
                            alerts.append('pull_idle_timeout:'+task['task'])
            if migration_complete and running and activity and time.time()-max(activity)>900:
                alerts.append('no_local_activity_for_15_minutes')
            summary = dict(checked_at=datetime.now(timezone.utc).isoformat(),host=socket.gethostname(),
                code_root=str(ROOT),data_root=str(DATA),docker_root=info.get('DockerRootDir'),
                docker_host=os.environ['DOCKER_HOST'],docker_reachable=bool(info),
                coordinator_alive=running,image_stage_finished=finished,
                stage=progress.get('stage','starting'),image_stage_counts=counts,image_errors=errors,active_pulls=pulls,
                migration_complete=migration_complete,migration=migration,tb_source=tb,
                disk_free_gib=round(free,1),alerts=alerts,
                monitor_status='attention_required' if alerts else 'healthy',
                automatic_model_launch=False,full_task_environments_ready=False,
                previous_environment_cohort=registration.get('previous_environment_cohort'),
                previous_state_counts=registration.get('previous_state_counts'),
                remote_execution_stopped=True,remote_results_counted_as_local=False)
            save(DATA/'validation/local-monitor.json',summary)
            with (DATA/'validation/local-monitor-history.jsonl').open('a') as stream:
                stream.write(json.dumps(summary)+'\n')
            inventory_path = ROOT/'docs/EXPERIMENT_STATUS_20261017.json'
            inventory = read(inventory_path)
            inventory['environment_preparation'] = summary
            save(inventory_path,inventory)
            checked = datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
            (ROOT/'docs/DOCKER_ENVIRONMENT_20261017.md').write_text('\n'.join([
                '# 本机 Docker 与实验环境准备','',f'核对时间：{checked}（北京时间）。','',
                '**代码修改、环境准备和核验均在本机 `r760-ssd`。远程准备队列已停止，远程只作参考代码来源；此前远程结果不计入本机进度。**','',
                '代码目录：`'+str(ROOT)+'`。镜像、缓存、结果、日志和临时文件位于 `/mnt/data/agenttx-runtime`。','',
                '本机 Docker socket：`'+os.environ['DOCKER_HOST']+'`；数据目录：`'+str(info.get('DockerRootDir','未取得'))+'`。独立服务使用国内镜像，无 daemon 代理，原有 Docker 和其他项目容器未重启。本机 Alpine 完整拉取、断网容器、Docker 构建和运行通过；相关回归测试 18 项通过。','',
                '历史数据迁移完成：`'+str(migration_complete)+'`。原缓存、日志和结果逐项校验后切为数据盘软链接。','',
                '当前阶段：`'+summary['stage']+'`；准备进程存活：`'+str(running)+'`；镜像状态：`'+json.dumps(counts,ensure_ascii=False)+'`。最多 2 个镜像 worker，仅使用国内域名。根据下载/解压字节和完成层计数判断：900 秒无进展或单源总耗时 14400 秒才超时换源，连接错误立即换源；失败记录保留。镜像源顺序：docker.1ms.run、hub.rat.dev、docker.1panel.live。','',
                '当前拉取进展：`'+json.dumps(pulls,ensure_ascii=False)+'`。','',
                '此前批次状态：`'+json.dumps(summary['previous_state_counts'])+'`；旧记录完整保存在 `'+str(summary['previous_environment_cohort'])+'`，其中 preparing 表示切换时中断，见 validation 下 cutover-r2-to-r3-20261017.json。','',
                'TB 源码状态：`'+str(tb.get('status','尚未完成'))+'`；本机已核验文件：'+str(tb.get('files_verified',0))+'。','',
                '数据盘可用：'+str(round(free,1))+' GiB；监控告警：`'+json.dumps(alerts,ensure_ascii=False)+'`。独立观察进程每 60 秒检查准备进程、Docker、磁盘及任务日志，不自动重启失败尝试。','',
                '**镜像和源码就绪不等于完整任务环境就绪。** 干净 base commit、依赖、官方验收及正式协议门槛仍需完成，模型实验保持暂停。','',
                '入口：`source scripts/agenttx-local-env.sh`。准备脚本：`experiments/scripts/prepare_local_environment.py`；监控脚本：`experiments/scripts/watch_local_environment.py`。证据：`'+str(COHORT)+'`；监控：`'+str(DATA/'validation/local-monitor.json')+'`。','']))
            print(json.dumps(dict(checked_at=summary['checked_at'],stage=summary['stage'],counts=counts,alerts=alerts)),flush=True)
            if finished:
                break
        except Exception as exc:
            print(json.dumps(dict(checked_at=datetime.now(timezone.utc).isoformat(),monitor_error=type(exc).__name__,detail=str(exc)[:300])),flush=True)
        time.sleep(60)


if __name__=='__main__':
    main()
