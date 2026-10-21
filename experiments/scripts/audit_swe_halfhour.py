#!/usr/bin/env python3
"""Half-hourly infrastructure and historical-effect review without outcome retries."""
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[2]
DATA=Path('/mnt/data/agenttx-runtime')
RUN=DATA/'results/sweagent-swe-local-monitored-20261018-full-r1'
OUT=DATA/'validation/swe-full-halfhour-20261018'


def read(path):
    return json.loads(path.read_text()) if path.exists() else {}


def alive(pid):
    if not pid:return False
    try:return (Path('/proc')/str(pid)/'stat').read_text().split(') ',1)[1][0]!='Z'
    except (OSError,IndexError):return False


def assess(progress, comparison, finished, coordinator_alive, previous, stamp, free_gib):
    alerts=[];effects={};control_effects={}
    if not finished and not coordinator_alive:alerts.append('coordinator_missing')
    if progress.get('stop_reason'):alerts.append('queue_stopped:'+progress['stop_reason'])
    if free_gib<250:alerts.append('disk_near_200_gib_guard')
    counts=progress.get('counts',{})
    if counts.get('infrastructure_error',0):alerts.append('infrastructure_errors:'+str(counts['infrastructure_error']))
    if comparison.get('environment_audit',{}).get('requires_infrastructure_review'):
        alerts.append('independent_environment_audit_requires_review')
    for label,value in [('progress',progress),('comparison',comparison)]:
        updated=value.get('updated_at',value.get('checked_at'))
        if not updated:alerts.append(label+'_missing');continue
        age=(stamp-datetime.fromisoformat(updated)).total_seconds()
        if not finished and age>180:alerts.append(label+'_stale')
    missing_workers=[]
    for row in progress.get('active',[]):
        if not alive(row.get('pid')):
            identity=row['task']+':'+row['policy']
            missing_workers.append(identity)
            # A completed process can disappear a few seconds before the
            # coordinator publishes its result. Only persistent absence warns.
            if identity in previous.get('missing_worker_observations',[]):
                alerts.append('worker_missing:'+identity)
    for row in progress.get('tasks',[]):
        if row['state']=='running' and row.get('started_at'):
            if (stamp-datetime.fromisoformat(row['started_at'])).total_seconds()>3690:
                alerts.append('cell_exceeds_registered_wall_limit:'+row['task']+':'+row['policy'])
    done=sum(counts.get(k,0) for k in ('completed','infrastructure_error'))
    old_done=previous.get('terminal_attempts')
    elapsed=(stamp-datetime.fromisoformat(previous['checked_at'])).total_seconds() if previous.get('checked_at') else 0
    if old_done is not None and elapsed>=1500 and done==old_done and counts.get('running') and not finished:
        alerts.append('no_terminal_progress_since_previous_halfhour_review')
    for policy,row in comparison.get('by_policy',{}).items():
        n=row['paired_valid_cells'];flags=[]
        if n<20:flags.append('sample_insufficient')
        elif row['paired_success_rate_delta_pp'] < -10:flags.append('success_rate_below_history_by_over_10_pp')
        if row['paired_both_success_with_usage']>=10 and row['paired_success_token_ratio']>1.25:
            flags.append('paired_success_tokens_over_1_25_times_history')
        effects[policy]=dict(paired_cells=n,success_rate_delta_pp=row['paired_success_rate_delta_pp'],
                            token_ratio=row['paired_success_token_ratio'],assessment=flags or ['within_review_thresholds'])
    for policy,row in comparison.get('within_cohort_controls',{}).items():
        if policy in ('clean_recovery','ideal_oracle'):continue
        n=row['both_success_pairs'];saving=row['token_reduction_pct']
        if n<20:
            state='preliminary_cost_advantage_small_sample' if saving is not None and saving>0 else 'insufficient_sample'
        elif row['paired_finished'] and row['causal_successes']<row['control_successes']-.1*row['paired_finished']:
            state='review_success_regression'
        else:state='cost_advantage_observed' if saving is not None and saving>0 else 'review_no_cost_advantage'
        control_effects[policy]=dict(assessment=state,both_success_pairs=n,token_reduction_pct=saving,
                                    causal_cheaper_tasks=row['causal_cheaper_tasks'])
    return dict(checked_at=stamp.isoformat(),counts=counts,active=progress.get('active',[]),
        terminal_attempts=done,new_terminal_attempts=None if old_done is None else done-old_done,
        missing_worker_observations=missing_workers,
        finished=finished,coordinator_alive=coordinator_alive,disk_free_gib=round(free_gib,1),
        alerts=alerts,effects=effects,within_cohort_controls=comparison.get('within_cohort_controls',{}),
        control_effect_assessments=control_effects,
        assessment='infrastructure_attention_required' if alerts else 'effect_review_required' if any(any(flag not in ('sample_insufficient','within_review_thresholds') for flag in r['assessment']) for r in effects.values()) else 'running_with_insufficient_effect_sample' if not effects or any('sample_insufficient' in r['assessment'] for r in effects.values()) else 'within_review_thresholds',
        thresholds=dict(min_paired_cells_per_policy=20,success_delta_review_below_pp=-10,
                        min_both_success_cost_pairs=10,token_ratio_review_above=1.25),
        no_outcome_based_stopping_or_retry=True,
        protocol_differences=comparison.get('protocol_differences',[]))


def main():
    global RUN
    pointer=read(DATA/'validation/active-swe-full-cohort.json')
    if pointer.get('cohort'):
        name=pointer['cohort'];assert Path(name).name==name and name.startswith('sweagent-swe-local-monitored-')
        RUN=DATA/'results'/name
    OUT.mkdir(exist_ok=True,parents=True)
    progress=read(RUN/'progress.json');comparison=read(RUN/'historical-comparison.json')
    previous=read(OUT/'latest.json');stamp=datetime.now(timezone.utc)
    import shutil
    review=assess(progress,comparison,(RUN/'execution.json').exists(),alive(progress.get('coordinator_pid')),
                  previous,stamp,shutil.disk_usage(DATA).free/2**30)
    payload=json.dumps(review,indent=2)+'\n'
    (OUT/(stamp.strftime('%Y%m%dT%H%M%S.%fZ')+'.json')).write_text(payload)
    tmp=OUT/'latest.tmp';tmp.write_text(payload);tmp.replace(OUT/'latest.json')
    local=stamp.astimezone(ZoneInfo('Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')
    lines=['# SWE-agent 每半小时巡检','',f'核验时间：{local}（北京时间）；当前批次 `{RUN.name}`。','',
           f"状态：`{review['counts']}`；新增终止尝试：{review['new_terminal_attempts']}；主进程存活：{review['coordinator_alive']}。",'',
           f"评估：`{review['assessment']}`；基础设施告警：`{review['alerts']}`；数据盘剩余 {review['disk_free_gib']} GiB。",'',
           '## Causal 对本批对照组','',
           '| 对照组 | 双方成功配对 | Causal token 减少 | Causal 更省题数 |',
           '| --- | ---: | ---: | ---: |']
    for policy,row in review['within_cohort_controls'].items():
        saving='—' if row['token_reduction_pct'] is None else f"{row['token_reduction_pct']:.1f}%"
        lines.append(f"| {policy} | {row['both_success_pairs']} | {saving} | {row['causal_cheaper_tasks']} |")
    lines+=['','以上 token 为 SWE-agent model_stats 的主会话及文档重放文本估算，未完整计入 API 推理用量；完整日志用量使用 audit_swe_token_usage.py 单独核对。只在同题且双方成功时比较成本；成功率和来源批次见每分钟报告。少量题目不能证明稳定优势或显著性。','',
           '## 历史对照巡检阈值','',
           '每策略至少 20 个历史有效配对后，成功率低于历史超过 10 个百分点标记复核；至少 10 个双方成功的成本配对后，token 比超过 1.25 标记复核。样本不足时不下效果结论。这些是巡检阈值，不是显著性检验。','',
           '效果差异只触发报告，不据此停跑、选择任务或自动重试。基础设施错误由实验调度器停止新增任务，保留现有尝试。历史时限、崩溃种子及代码不同，不能将配对差值视为严格复现证据。','',
           '巡检由本机 systemd 用户定时器运行，每逢整点和半点执行；快照写入数据盘，不主动发送聊天消息。','',
           '[每分钟配对报告](SWE_FULL_COMPARISON_20261018.md)','',
           '完整巡检：`'+str(OUT/'latest.json')+'`。','']
    (ROOT/'docs/SWE_HALFHOUR_REVIEW_20261018.md').write_text('\n'.join(lines))
    print(json.dumps(review),flush=True)
    pending=read(DATA/'validation/swe-full-continuation-pending.json')
    if review['finished'] and pending.get('previous_cohort')!=RUN.name:
        subprocess.run(['systemctl','--user','stop','agenttx-swe-halfhour.timer'],check=False)


if __name__=='__main__':main()
