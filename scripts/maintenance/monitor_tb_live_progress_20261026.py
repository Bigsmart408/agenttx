"""Read-only live progress sampling with a 30-minute terminal-result alert."""
import datetime
import json
from pathlib import Path
import time

D=Path('/mnt/data/agenttx-runtime')
AUDIT=D/'validation/tb-live-monitor-20261026'


def coordinator_status(progress,now,proc_root=Path('/proc')):
    pid=progress['coordinator_pid'];state=None
    try:state=(proc_root/str(pid)/'stat').read_text().split(') ',1)[1].split()[0]
    except (OSError,IndexError):pass
    age=now-datetime.datetime.fromisoformat(progress['updated_at']).timestamp()
    healthy=state not in (None,'Z','T','t') and age<=90
    return dict(coordinator_pid=pid,coordinator_state=state,progress_age_s=round(age),coordinator_healthy=healthy)


def sample():
    now=time.time()
    root=Path(json.loads((D/'validation/active-tb-dual-native.json').read_text())['root'])
    p=json.loads((root/'progress.json').read_text())
    rows=p['tasks']+p['retry_tasks']
    real=[r for r in rows if r['state'] in ('completed','infrastructure_error')
          and r.get('finished_at') and not r.get('interrupted_at_user_request')]
    last=max((datetime.datetime.fromisoformat(r['finished_at']).timestamp() for r in real),default=now)
    workers=[]
    for r in rows:
        if r['state']!='running':continue
        folder=Path(r['result_path']).parent
        recovery=folder/'container.recovery.stdout.log'
        logs=list(folder.glob('session/upperdir/app/.agenttx-codex-*/codex/sessions/*/*/*/*.jsonl')) if r['harness']=='codex' else [recovery if recovery.exists() else folder/'container.stdout.log']
        logs=[f for f in logs if f.exists()]
        native={}
        for name in ('model-result.json','replay-result.json','result.json','host-result.json'):
            f=folder/name
            if f.exists():
                data=json.loads(f.read_text())
                native[name]=dict(returncode=data.get('returncode'),status=data.get('status'),age_s=round(now-f.stat().st_mtime))
        workers.append(dict(task=r['task'],harness=r['harness'],policy=r['policy'],
            elapsed_s=round(now-datetime.datetime.fromisoformat(r['started_at']).timestamp()),
            model_log_age_s=round(now-max(f.stat().st_mtime for f in logs)) if logs else None,
            native=native,result_path=r['result_path']))
    health=coordinator_status(p,now)
    return dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),cohort=root.name,**health,
        counts=p['counts'],retry_counts=p['retry_counts'],active_count=len(p['active']),
        stop_reason=p['stop_reason'],provider_gate=p.get('provider_gate'),
        seconds_without_real_terminal=round(now-last),investigation_due=bool(p['stop_reason']) or now-last>=1800 or not health['coordinator_healthy'],
        last_real_terminal_at=datetime.datetime.fromtimestamp(last,datetime.timezone.utc).isoformat(),
        workers=workers,disk_free_gib=p.get('disk_free_gib'),no_model_calls=True)


def main():
    AUDIT.mkdir(parents=True,exist_ok=True)
    previous=None;last_alert=0
    while True:
        row=sample()
        with (AUDIT/'samples.jsonl').open('a') as f:f.write(json.dumps(row)+'\n')
        tmp=AUDIT/'latest.tmp';tmp.write_text(json.dumps(row,indent=2)+'\n');tmp.replace(AUDIT/'latest.json')
        signature=(row['cohort'],tuple(sorted(row['counts'].items())),tuple(sorted(row['retry_counts'].items())),row['stop_reason'],row['coordinator_healthy'])
        if signature!=previous or (row['investigation_due'] and time.monotonic()-last_alert>=300):
            print(json.dumps({k:v for k,v in row.items() if k!='workers'}),flush=True)
            if row['investigation_due']:last_alert=time.monotonic()
        previous=signature
        time.sleep(30)


if __name__=='__main__':main()
