#!/usr/bin/env python3
"""Independent read-only observation of local TB builds and SWE launch cells."""
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import socket
import subprocess
import time
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
DATA=Path('/mnt/data/agenttx-runtime')
TB=DATA/'validation/tb-images-local-20261018-r1'
TB_REPAIRS=DATA/'validation/tb-network-repairs-20261018-r1'
SWE=DATA/'results'/os.environ.get('AGENTTX_SWE_MONITORED_RUN','sweagent-swe-local-monitored-20261018-r2')

def read(path):
    try:return json.loads(path.read_text())
    except FileNotFoundError:return {}

def alive(pid):
    try:return (Path('/proc')/str(pid)/'stat').read_text().split(') ',1)[1][0]!='Z'
    except (OSError,ValueError):return False

def main():
    assert socket.gethostname()=='r760-ssd'
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    lock=(DATA/'validation/local-campaign-watch.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    while True:
        try:
            tb=read(TB/'progress.json');swe=read(SWE/'progress.json')
            audit=read(SWE/'postrun-audit.json')
            repairs=read(TB_REPAIRS/'execution.json').get('tasks',[])
            repaired={r['task'] for r in repairs if r['state']=='image_ready'}
            ready={r['task'] for r in tb.get('tasks',[]) if r['state']=='image_ready'} | repaired
            unresolved=[r for r in tb.get('tasks',[]) if r['state'] in ('environment_error','unsupported_environment') and r['task'] not in repaired]
            swe_successful=sum(r.get('success') is True for r in swe.get('tasks',[]) if r['state']=='completed')
            swe_task_failures=sum(r.get('success') is False for r in swe.get('tasks',[]) if r['state']=='completed')
            tb_done=(TB/'execution.json').exists();swe_done=(SWE/'execution.json').exists()
            docker=subprocess.run(['docker','info','--format','{{json .DockerRootDir}}'],capture_output=True,text=True,timeout=20)
            alerts=[]
            if docker.returncode or docker.stdout.strip()!='"/mnt/data/agenttx-runtime/docker"':alerts.append('docker_unavailable_or_wrong_storage')
            for label,progress,done in [('tb',tb,tb_done),('swe',swe,swe_done)]:
                if progress and not done and not alive(progress.get('coordinator_pid')):alerts.append(label+'_coordinator_missing')
            if swe.get('stop_reason') not in (None,'canary_complete_formal_protocol_gates_pending'):alerts.append('swe:'+swe['stop_reason'])
            if audit.get('requires_infrastructure_review'):alerts.append('swe:postrun_infrastructure_review_required')
            if unresolved:alerts.append('tb:unresolved_environment_errors:'+str(len(unresolved)))
            free=os.statvfs(DATA).f_bavail*os.statvfs(DATA).f_frsize/2**30
            if free<250:alerts.append('data_disk_near_200_gib_guard')
            summary=dict(checked_at=datetime.now(timezone.utc).isoformat(),host=socket.gethostname(),
                tb_counts=tb.get('counts'),tb_finished=tb_done,tb_coordinator_alive=alive(tb.get('coordinator_pid')),
                tb_active=[r for r in tb.get('tasks',[]) if r['state']=='preparing'],
                tb_environment_errors=[r for r in tb.get('tasks',[]) if r['state'] in ('environment_error','unsupported_environment')],
                tb_images_ready_unique=len(ready),tb_repaired_tasks=sorted(repaired),tb_unresolved_environment_errors=unresolved,
                swe_cohort=SWE.name,swe_counts=swe.get('counts'),swe_stage=swe.get('stage','not_started'),swe_finished=swe_done,
                swe_postrun_audit=audit,
                swe_successful=swe_successful,swe_completed_task_failures=swe_task_failures,
                swe_coordinator_alive=alive(swe.get('coordinator_pid')),swe_active=swe.get('active'),
                swe_stop_reason=swe.get('stop_reason'),formal_eligible=False,disk_free_gib=round(free,1),alerts=alerts)
            dest=DATA/'validation/local-campaign-monitor.json';tmp=dest.with_suffix('.tmp')
            tmp.write_text(json.dumps(summary,indent=2)+'\n');tmp.replace(dest)
            with (DATA/'validation/local-campaign-monitor-history.jsonl').open('a') as f:f.write(json.dumps(summary)+'\n')
            stamp=datetime.now(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
            (ROOT/'docs/LOCAL_CAMPAIGNS_20261018.md').write_text('\n'.join([
                '# 本机 TB 镜像与 SWE-agent 启动监控','',f'更新时间：{stamp}（北京时间）。','',
                '执行机器 r760-ssd；代码在系统盘，Docker、缓存、实验结果、日志和临时文件在 /mnt/data/agenttx-runtime。','',
                'SWE 镜像 300/300 已完成，TB 正在构建。TB 状态：`'+json.dumps(summary['tb_counts'])+'`；准备进程存活：'+str(summary['tb_coordinator_alive'])+'。','',
                'TB 活动任务：`'+json.dumps(summary['tb_active'],ensure_ascii=False)+'`。环境错误数量：'+str(len(summary['tb_environment_errors']))+'。','',
                '计入独立修复后，TB 已有 '+str(len(ready))+'/241 个任务镜像就绪；修复任务：`'+json.dumps(sorted(repaired))+'`；仍未解决的准备错误：'+str(len(unresolved))+'。原队列的失败状态保留。','',
                'SWE-agent 状态：`'+str(summary['swe_stage'])+'`；单元计数：`'+json.dumps(summary['swe_counts'])+'`；活动单元：`'+json.dumps(summary['swe_active'])+'`。','',
                '已通过最终验收 '+str(swe_successful)+' 项；已结束但未通过 '+str(swe_task_failures)+' 项。五单元验证后的计划内协议等待不视为异常中断。','',
                '验收复核：`'+json.dumps(audit,ensure_ascii=False)+'`。completed 只表示尝试结束，不能等同于基础设施有效或任务通过。','',
                'SWE 完整范围 300×5=1500，当前仅安排首个固定任务的五策略受监控启动。先运行 clean，再运行其余四策略；基础设施异常停止加新单元，任务失败保留且不按结果重试。五单元结束后等待正式协议门槛，不自动扩到全量。','',
                '模型 gpt-5.6-luna / max，每次模型会话 900 秒，无步数或调用次数 cap；token 硬预算和策略 payload 计数匹配尚未实现，本批 formal_eligible=false。agent 在本机隔离挂载命名空间，官方 verifier 在固定镜像内。','',
                '停止原因：`'+str(summary['swe_stop_reason'])+'`；告警：`'+json.dumps(alerts,ensure_ascii=False)+'`；数据盘剩余 '+str(round(free,1))+' GiB。','',
                '监控每 60 秒刷新，不自动重启或改写失败。进程存活不等于下载持续有进展，TB 的构建最终以镜像核验记录为准。','',
                'TB 证据：`'+str(TB)+'`。SWE 证据：`'+str(SWE)+'`。','']))
            print(json.dumps(summary),flush=True)
            if tb_done and swe_done:break
        except Exception as exc:print(type(exc).__name__+': '+str(exc),flush=True)
        time.sleep(60)

if __name__=='__main__':main()
