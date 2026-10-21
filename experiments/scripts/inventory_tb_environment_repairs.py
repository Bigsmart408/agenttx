#!/usr/bin/env python3
"""Create an immutable readiness inventory from specific local repair attempts."""
import argparse
import json
import os
import subprocess
from pathlib import Path
from experiments.scripts.validate_tb_ready_local import DATA,digest,save,now
from experiments.scripts.audit_tb_task_baselines import validate_audit
from experiments.workloads import terminal_bench_suite as tb

PATTERNS=[
 'tb-adapter-bootstrap-20261020-r1/*/result.json',
 'tb-bootstrap-network-20261020-r1/*/result.json',
 'tb-missing-repair-20261020-r4/bootstrap/*/result.json',
 'tb-sidecar-official-20261020-r[34]/*/result.json',
 'tb-fixgit-image-repair-20261020-r2/bootstrap/*/result.json',
 'tb-qemu-repair-20261020-r6/bootstrap/*/result.json',
 'tb-qemu-bootstrap-20261020-r7/*/result.json',
 'tb-qemu-ssh-bootstrap-20261020-r8/*/result.json',
 'tb-flink-repair-20261020-r6/bootstrap/*/result.json',
 'tb-flink-image-repair-20261020-r7/bootstrap/*/result.json',
 'tb-neuron-bootstrap-20261020-r2/*/result.json',
 'tb-mnist-bootstrap-20261020-r3/*/result.json',
 'tb-c4-bootstrap-20261020-r2/*/result.json',
 'tb-c4-bootstrap-20261020-r3/*/result.json',
 'tb-c4-bootstrap-20261020-r4/*/result.json',
 'tb-c4-bootstrap-20261020-r5/*/result.json',
 'tb-spark-bootstrap-20261020-r2/*/result.json',
 'tb-task-baseline-audit-20261020-r1/*/result.json',
 'tb-task-baseline-audit-20261020-r2/*/result.json',
 'tb-intended-fault-audit-20261020-r2/*/result.json',
 'tb-accelerate-audit-20261020-r3/*/result.json',
 'tb-lc0-image-repair-20261020-r1/bootstrap/*/result.json',
 'tb-lc0-image-repair-20261020-r[34]/bootstrap/*/result.json',
 'tb-win311-image-repair-20261020-r1/bootstrap/*/result.json',
 'tb-win311-audit-20261020-r3/*/result.json',
 'tb-xp-image-repair-20261020-r1/bootstrap/*/result.json',
 'tb-lc0-audit-20261020-r2/*/result.json',
 'tb-runtime-git-audit-20261020-r1/*/result.json',
]


def main():
    assert os.environ.get('DOCKER_HOST')=='unix:///run/agenttx-docker/docker.sock'
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--previous',type=Path,default=DATA/'validation/tb-usability-inventory-20261020-r1.json')
    args=p.parse_args();assert args.output.resolve().is_relative_to(DATA/'validation')
    assert not args.output.exists()
    old=json.loads(args.previous.read_text());tasks=tb.load_tasks(DATA/'cache')
    images={}
    for line in subprocess.check_output(['docker','image','ls','--no-trunc','--format','{{.Repository}}:{{.Tag}} {{.ID}}'],text=True).splitlines():
        tag,identity=line.split();images[tag]=identity
    built={}
    for task in tasks.values():
        tag=tb._official_image_tag(task,tb.task_source(DATA/'cache',task))
        if tag in images:built[task.task_id]=images[tag]
    baseline={r['task']:r for r in old['official_baseline_tasks']}
    previous_ready=set(baseline)|set(old['official_positive_negative'])
    audited={}
    paths={p for pattern in PATTERNS for p in (DATA/'validation').glob(pattern)}
    for path in sorted(paths):
        row=json.loads(path.read_text());name=row['task']
        if not row.get('official_tests_executed') and not row.get('task_baseline_audit'):continue
        source=tb.task_source(DATA/'cache',tasks[name])
        assert row['image_id']==built[name],name+' image identity differs'
        assert row['original_script_sha256']==digest(source/'run-tests.sh')
        item=dict(task=name,evidence=str(path),sha256=digest(path),classification=row['baseline_classification'])
        if row.get('official_tests_executed'):baseline[name]=item
        else:
            validate_audit(row,source);audited[name]=item
    for name in baseline:audited.pop(name,None)
    ready=set(baseline)|set(audited)|set(old['official_positive_negative'])
    blocked=sorted(set(tasks)-ready)
    row=dict(checked_at=now(),previous_inventory=str(args.previous),previous_inventory_sha256=digest(args.previous),
        image_identity_verified=len(built),unbuilt=len(tasks)-len(built),missing_images=sorted(set(tasks)-built.keys()),
        official_bootstrap_executed=len(baseline)+len(old['official_positive_negative']),
        official_positive_negative=old['official_positive_negative'],official_baseline_tasks=list(baseline.values()),
        audited_task_baseline_tasks=list(audited.values()),dispatch_ready_tasks=len(ready),blocked_tasks=blocked,
        newly_verified_tasks=sorted(ready-previous_ready),formal_six_arm_model_eligibility=False,
        definition='Readiness includes actual official-test execution or individually audited task-required baseline faults with dual native preflights. No task-success gate. Audits are not counted as executed tests.',
        model_calls=0,task_solutions_applied=False,source_sha256=digest(Path(__file__)))
    save(args.output,row)
    print(json.dumps({k:row[k] for k in ['checked_at','image_identity_verified','official_bootstrap_executed','dispatch_ready_tasks','blocked_tasks']}),flush=True)


if __name__=='__main__':main()
