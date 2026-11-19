#!/usr/bin/env python3
"""Monitor factorial evidence and resume pending work after verified service recovery."""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'src'),str(Path(__file__).parent)]
from scripts.maintenance import supervise_selected_effects_full as shared
from experiments.scripts.analyze_recovery_cross_ablation import analyze


def resume(cohort,audit):
    assert not shared.read(cohort/'progress.json')['active']
    shared.verify_sources(cohort)
    for name in ('progress.json','cells.json','execution.json'):
        if (cohort/name).exists():
            shutil.copy2(cohort/name,audit/('before-'+name))
    if (cohort/'execution.json').exists():
        (cohort/'execution.json').rename(audit/'previous-execution.json')
    registration=shared.read(cohort/'registration.json')
    service='agenttx-recovery-cross-resume-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    source=Path(registration['frozen_source'])
    classified_launcher=cohort/'dispatch-classification-launch.py'
    launch=([str(classified_launcher)] if classified_launcher.exists() else
            [str(source/'experiments/scripts/launch_recovery_cross_ablation.py'),'run','--cohort',str(cohort)])
    subprocess.run(['systemd-run','--user','--unit='+service,
        '--property=WorkingDirectory='+str(source),
        '--property=StandardOutput=append:'+str(cohort/'coordinator.log'),
        '--property=StandardError=append:'+str(cohort/'coordinator.log'),
        '/home/bfq/.venvs/agenttx/bin/python','-u',*launch],check=True,capture_output=True,timeout=30)
    services=shared.read(cohort/'services.json')
    services['coordinator']=service+'.service'
    shared.save(cohort/'services.json',services)
    shared.save(audit/'resume.json',dict(pending_only=True,terminal_attempts_retried=0,service=service))
    return service+'.service'


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cohort',type=Path,required=True)
    args=p.parse_args()
    cohort=args.cohort
    shared.resume=resume
    directory=cohort/'supervisor'
    directory.mkdir(exist_ok=True)
    (cohort/'monitor').mkdir(exist_ok=True)
    control_path=directory/'control.json'
    while True:
        control=shared.read(control_path) if control_path.exists() else {}
        try:
            shared.verify_sources(cohort)
            if (cohort/'progress.json').exists():
                status=shared.tick(cohort,control)
                progress=status.get('progress') or {}
                reason=progress.get('stop_reason') or ''
                if (status['state']=='stopped_without_new_reviewed_failure'
                        and '_disk_below_' in reason and not progress.get('active')):
                    registration=shared.read(cohort/'registration.json')
                    locations={'output':cohort,'docker':Path('/mnt/data/agenttx-runtime'),
                               'host':Path('/home/bfq')}
                    free={k:shutil.disk_usage(p).free/2**30 for k,p in locations.items()}
                    enough=all(free[k]>=2*limit for k,limit in registration['disk_guards_gib'].items())
                    if enough:
                        audit=cohort/'maintenance'/('disk-recovered-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
                        audit.mkdir(parents=True)
                        shared.save(audit/'disk-health.json',dict(free_gib=free,
                            guards_gib=registration['disk_guards_gib'],required_margin=2))
                        service=resume(cohort,audit)
                        status=dict(state='pending_resumed_after_disk_recovery',
                                    coordinator=service,audit=str(audit),progress=progress)
                    else:
                        status=dict(state='waiting_for_disk_space',free_gib=free,progress=progress)
            else:
                service=shared.read(cohort/'services.json')['coordinator']
                active=subprocess.run(['systemctl','--user','show',service,
                    '--property=ActiveState','--value'],capture_output=True,text=True,timeout=15).stdout.strip()
                status=dict(state='environment_preflight' if active in ('active','activating')
                            else 'preflight_error_requires_review',registered_cells=4328)
            result=analyze(cohort)
            shared.save(cohort/'analysis.json',result)
            status.update(analysis_counts=result['counts'],quartets={k:v['verified_quartets'] for k,v in result['groups'].items()})
        except Exception as exc:
            status=dict(state='monitor_error_requires_review',error=f'{type(exc).__name__}: {exc}')
        status['checked_at']=shared.now()
        shared.save(directory/'latest.json',status)
        shared.save(cohort/'monitor/latest.json',status)
        shared.save(control_path,control)
        with (directory/'history.jsonl').open('a') as f:
            f.write(json.dumps(status)+'\n')
        if status['state']=='finished':
            return
        time.sleep(60)


if __name__=='__main__':
    main()
