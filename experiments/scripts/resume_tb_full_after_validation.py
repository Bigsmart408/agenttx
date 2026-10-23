#!/usr/bin/env python3
"""Apply the user's explicit full-run authorization after drain and capacity repair."""
from datetime import datetime,timezone
import fcntl
import hashlib
import json
from pathlib import Path
import shlex
import shutil
import subprocess
import time

from experiments.scripts.run_tb_dual_local import DATA,PYTHON,ROOT,save


def now():return datetime.now(timezone.utc).isoformat()


def main():
    audit=DATA/'validation/tb-full-resume-20261021-r15'
    validation=DATA/'validation/tb-trend-validation-20261021-r1'
    previous=DATA/'results/tb-dual-native-full-20261021-r14'
    cold=DATA/'validation/tb-cold-archive-20261021-tb-dual-native-full-20261020-r12'
    hold=DATA/'validation/tb-full-dispatch-hold.json'
    assert audit.is_dir() and hold.is_file()
    assert (previous/'execution.json').is_file()
    save(audit/'authorization.json',dict(authorized_at=now(),user_instruction='ok，你开始跑全量吧；ideal你跑了吗；还要clean',
        validation_summary_scope='User authorized full after three positive primary trend comparisons on59/60 observations; final Clean remains pending.',
        full_name='tb-dual-native-full-20261021-r15',previous_cohort=previous.name,
        policies=['clean_recovery','crash_no_rollback','temporal_checkpoint','whole_branch_abort','causal','ideal_oracle'],
        workers_total=8,pending_only=True,validation_results_fill_full=False,outcome_based_retry=False))
    while True:
        gate=json.loads((validation/'trend-gate.json').read_text())
        assert all(c['passes'] for c in gate['comparisons'].values()), 'Primary trend changed; inspect before release'
        finished=(validation/'execution.json').is_file()
        archived=(cold/'execution.json').is_file()
        free=shutil.disk_usage(DATA).free/2**30
        ready=finished and archived and free>=400
        save(audit/'progress.json',dict(checked_at=now(),phase='ready' if ready else 'waiting_for_dependencies',
            validation_finished=finished,cold_archive_verified=archived,disk_free_gib=round(free,1),
            minimum_launch_free_gib=400,registered_dispatch_guard_gib=350,full_started=False))
        if ready:break
        time.sleep(15)
    # No source that the validation executed may change before launch. Added
    # maintenance scripts have their own new full-run source registration.
    manifest=json.loads((validation/'source-manifest.json').read_text())
    for entry in manifest['files']:
        assert hashlib.sha256((ROOT/entry['path']).read_bytes()).hexdigest()==entry['sha256'], entry['path']
    check=(DATA/'validation/tb-dual-native.lock').open('a')
    fcntl.flock(check,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(check,fcntl.LOCK_UN);check.close()
    old_hold=hold.read_bytes();(audit/'released-hold.json').write_bytes(old_hold)
    assert json.loads(old_hold)['cohort']==previous.name
    assert not (DATA/'results/tb-dual-native-full-20261021-r15').exists()
    hold.unlink()
    cmd=[str(PYTHON),'-u',str(ROOT/'experiments/scripts/run_tb_dual_local.py'),
         '--name','tb-dual-native-full-20261021-r15','--resume-from',str(previous),
         '--inventory',str(DATA/'validation/tb-usability-inventory-20261021-r14.json')]
    launch=['systemd-run','--user','--unit=agenttx-tb-dual-full-20261021-r15',
            '--property=WorkingDirectory='+str(ROOT),'/bin/bash','-c',
            'source scripts/agenttx-local-env.sh; export PYTHONPATH=/home/bfq/agenttx/src:/home/bfq/agenttx; exec '+shlex.join(cmd)]
    try:subprocess.run(launch,cwd=ROOT,check=True,timeout=30)
    except BaseException:
        if not hold.exists():hold.write_bytes(old_hold)
        raise
    full=DATA/'results/tb-dual-native-full-20261021-r15'
    deadline=time.monotonic()+120
    while True:
        path=full/'progress.json'
        actual=json.loads(path.read_text()) if path.is_file() else {}
        if actual.get('counts',{}).get('running',0):break
        if (full/'execution.json').exists() or time.monotonic()>deadline:
            raise RuntimeError('Full launch did not produce active workers; inspect the registered new service')
        time.sleep(2)
    save(audit/'execution.json',dict(started_at=now(),new_service='agenttx-tb-dual-full-20261021-r15.service',
        validation_execution=str(validation/'execution.json'),validation_full_panel_gate=gate['gate_passed'],
        primary_trends_passed=True,user_authorized_full=True,cold_archive=str(cold/'execution.json'),
        source_validated_files_unchanged=True,raw_results_modified=False,pending_only=True,
        observed_full_counts=actual['counts']))
    save(audit/'progress.json',dict(checked_at=now(),phase='full_running',full_started=True,
        full_root=str(full),observed_full_counts=actual['counts'],
        disk_free_gib=round(shutil.disk_usage(DATA).free/2**30,1)))


if __name__=='__main__':main()
