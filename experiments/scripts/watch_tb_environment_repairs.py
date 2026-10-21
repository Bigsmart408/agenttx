#!/usr/bin/env python3
"""Keep local TB repair evidence current while bounded no-model jobs finish."""
from collections import Counter
from datetime import datetime,timezone
import argparse,json,os,shutil,time
from pathlib import Path
from experiments.scripts.validate_tb_ready_local import DATA,save,digest

BATCHES={
 'official_bootstrap':('tb-bootstrap-repair-20261019-r1',45),
 'sidecars':('tb-sidecar-repair-20261019-r1',12),
 'opencv_library_repair':('tb-bootstrap-fix-libgl-20261019-r2',1),
 'wheel_mirror_repair':('tb-bootstrap-wheel-mirror-repair-20261019-r3',2),
 'celery_byte_progress_repair':('tb-sidecar-pull-repair-20261019-r2',1),
 'sidecar_short_deadline_followup':('tb-sidecar-byte-repairs-20261019-r3',0),
 'sam_git_transport_repair':('tb-bootstrap-sam-transport-repair-20261019-r4',1),
 'sam_verified_archive_repair':('tb-bootstrap-sam-archive-repair-20261019-r5',1),
 'spring_base_pull_repair':('tb-sidecar-spring-base-repair-20261019-r4',1),
 'spring_compose_plugin_repair':('tb-sidecar-spring-compose-repair-20261019-r5',1),
 'remaining_bootstrap_mirror_repair':('tb-bootstrap-index-repair-20261019-r6',4),
 'basic_probe_official_validation':('tb-basic-official-validation-20261019-r1',156),
}


def snapshot():
    result=dict(checked_at=datetime.now(timezone.utc).isoformat(),host='r760-ssd',model_calls=0,
                raw_attempts_preserved=True,formal_model_eligibility=False,batches={})
    done=True
    for label,(name,total) in BATCHES.items():
        root=DATA/'validation'/name
        registration=root/'registration.json'
        if registration.exists():
            registered=json.loads(registration.read_text())
            if registered.get('dynamic_total'):total=len(registered['tasks'])
        rows=[json.loads(p.read_text()) for p in root.glob('*/result.json')]
        finished=(root/'execution.json').exists();done=done and finished
        result['batches'][label]=dict(root=str(root),finished=finished,completed=len(rows),total=total,
            counts=dict(Counter(r.get('baseline_classification') or ('passed' if r.get('passed') else 'failed') for r in rows)),
            tasks=[dict(task=r['task'],classification=r.get('baseline_classification'),passed=r.get('passed'),
                        error=r.get('error'),official_tests_executed=r.get('official_tests_executed')) for r in rows])
    result['adapter_repairs']={}
    sources=[DATA/'validation/tb-adapter-repair-20261019-r1/accelerate-maximal-square/inside-result.json']
    sources+=list((DATA/'validation/tb-user-adapter-repair-20261019-r6').glob('*/inside-result.json'))
    for p in sources:
        if p.exists():
            d=json.loads(p.read_text())
            result['adapter_repairs'][p.parent.name]=dict(transaction_ok=d.get('transaction_ok'),
                task_identity=d.get('original_task_identity'),permissions_verified=d.get('task_user_permissions_verified'),
                rollback_verified=d.get('rollback_verified'),commit_owner_verified=d.get('commit_file_owner_verified'),
                session_user_restored=d.get('session_task_user_restored'),source=str(p),sha256=digest(p))
    result['data_disk_free_gib']=round(shutil.disk_usage(DATA).free/2**30,1)
    result['all_batches_finished']=done
    out=DATA/'validation/tb-environment-repair-20261019';out.mkdir(exist_ok=True)
    save(out/'latest.json',result)
    with (out/'history.jsonl').open('a') as f:f.write(json.dumps(result)+'\n')
    return done


def main():
    p=argparse.ArgumentParser();p.add_argument('--once',action='store_true');args=p.parse_args()
    while True:
        finished=snapshot()
        if args.once or finished:return
        time.sleep(30)

if __name__=='__main__':main()
