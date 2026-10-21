#!/usr/bin/env python3
"""Persist local TB health and half-hour reviews without retrying model cells."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import time

DATA=Path('/mnt/data/agenttx-runtime')
OUT=DATA/'validation/tb-dual-native-monitor-20261019'


def save(path,value):
    temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(value,indent=2)+'\n');temporary.replace(path)


def alive(pid):
    try:os.kill(int(pid),0);return True
    except (OSError,TypeError,ValueError):return False


def snapshot():
    pointer=json.loads((DATA/'validation/active-tb-dual-native.json').read_text())
    root=Path(pointer['root']);progress=json.loads((root/'progress.json').read_text())
    active=progress.get('active',[]);alerts=[]
    finished=(root/'execution.json').exists()
    if not finished and not alive(progress['coordinator_pid']):alerts.append('coordinator_missing')
    if progress.get('stop_reason'):alerts.append(progress['stop_reason'])
    if any(not alive(r['pid']) for r in active):alerts.append('worker_exit_awaiting_reconciliation')
    if shutil.disk_usage(DATA).free<350*2**30:alerts.append('data_disk_below_dispatch_guard')
    builds=DATA/'validation/tb-images-local-20261019-r3/progress.json'
    handoffs=[]
    for path in (DATA/'validation').glob('tb-environment-continuation-*/registration.json'):
        registration=json.loads(path.read_text())
        if registration['from_cohort']==pointer['cohort'] and not (path.parent/'execution.json').exists():
            drain=path.parent/'drain-progress.json'
            if drain.exists():handoffs.append(dict(audit=str(path.parent),**json.loads(drain.read_text())))
    inventories=list((DATA/'validation').glob('tb-usability-inventory-*.json'))
    latest=max(inventories,key=lambda p:p.stat().st_mtime) if inventories else None
    readiness=json.loads(latest.read_text()) if latest else {}
    return dict(checked_at=datetime.now(timezone.utc).isoformat(),cohort=pointer['cohort'],
        canary=pointer['canary'],counts=progress['counts'],active_workers=len(active),
        coordinator_alive=alive(progress['coordinator_pid']),finished=finished,
        alerts=alerts,disk_free_gib=round(shutil.disk_usage(DATA).free/2**30,1),
        image_preparation=dict(image_ready=readiness.get('image_identity_verified'),
            unbuilt=readiness.get('unbuilt'),missing_images=readiness.get('missing_images')),
        historical_image_batch=dict(source=str(builds),counts=json.loads(builds.read_text())['counts']) if builds.exists() else None,
        environment_handoff=handoffs,
        prepared_environment=dict(inventory=str(latest) if latest else None,
            images=readiness.get('image_identity_verified'),
            official_tests_executed=readiness.get('official_bootstrap_executed'),
            dispatch_ready=readiness.get('dispatch_ready_tasks',readiness.get('official_bootstrap_executed'))),
        effects=json.loads((root/'effects.json').read_text()) if (root/'effects.json').exists() else None,
        context_map=dict(causal='compact',ideal_oracle='compact',clean_recovery='compact',
                         crash_no_rollback='full',temporal_checkpoint='full',whole_branch_abort='full'),
        formal_eligible=False,automatic_model_retry=False)


def main():
    OUT.mkdir(exist_ok=True)
    while True:
        try:
            state=snapshot();save(OUT/'latest.json',state)
            stamp=int(time.time())//1800*1800
            review=OUT/('review-'+str(stamp)+'.json')
            if not review.exists():save(review,state)
        except Exception as exc:
            save(OUT/'last-error.json',dict(checked_at=datetime.now(timezone.utc).isoformat(),error=repr(exc)))
        time.sleep(30)


if __name__=='__main__':main()
