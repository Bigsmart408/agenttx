#!/usr/bin/env python3
"""Check official bootstrap for basic-probe tasks, with bounded disk-aware dispatch."""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from datetime import datetime,timezone
import json,os,shutil,traceback
from pathlib import Path
from experiments.scripts.validate_tb_ready_local import DATA,save,digest
from experiments.scripts.validate_tb_bootstrap_batch import run

OUTPUT=DATA/'validation/tb-basic-official-validation-20261019-r1'
MIN_FREE_GIB=350


def main():
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    OUTPUT.mkdir(exist_ok=False)
    summary=DATA/'validation/tb-ready-validation-summary-20261018.json'
    selected=sorted(r['task'] for r in json.loads(summary.read_text())['checks']
                    if r['classification']=='runtime_and_tests_probed')
    assert len(selected)==156
    evidence=json.loads((DATA/'validation/tb-readiness-recheck-20261018.json').read_text())
    records={r['task']:r for r in evidence['checks'] if r['identity_verified']}
    assert all(t in records for t in selected)
    save(OUTPUT/'registration.json',dict(tasks=selected,workers=2,timeout_s=900,
        min_free_gib=MIN_FREE_GIB,model_calls=0,gold_solution_used=False,
        source_sha256=digest(Path(__file__)),
        selection_evidence=str(summary),selection_sha256=digest(summary),
        purpose='First official bootstrap check for basic-probe tasks; immutable baseline, no model outcomes retried.'))
    pending=list(selected);active={};rows=[];stop_reason=None
    def progress():
        save(OUTPUT/'progress.json',dict(updated_at=datetime.now(timezone.utc).isoformat(),
            completed=len(rows),total=len(selected),running=len(active),pending=len(pending),
            active=list(active.values()),stop_reason=stop_reason,
            disk_free_gib=round(shutil.disk_usage(DATA).free/2**30,1),tasks=rows))
    with ThreadPoolExecutor(max_workers=2) as pool:
        while pending or active:
            if pending and shutil.disk_usage(DATA).free<MIN_FREE_GIB*2**30:
                stop_reason='data_disk_below_350_gib_dispatch_guard'
            while pending and len(active)<2 and not stop_reason:
                task=pending.pop(0)
                active[pool.submit(run,records[task],OUTPUT,900,'https://mirrors.aliyun.com/pypi/simple')]=task
            progress()
            if not active:break
            finished,_=wait(active,timeout=10,return_when=FIRST_COMPLETED)
            for future in finished:
                task=active.pop(future)
                try:row=future.result()
                except Exception as exc:
                    folder=OUTPUT/task;folder.mkdir(exist_ok=True)
                    (folder/'coordinator-error.txt').write_text(traceback.format_exc())
                    row=dict(task=task,baseline_classification='environment_preparation_error',
                        official_tests_executed=False,error=repr(exc),model_calls=0,gold_solution_used=False)
                    save(folder/'result.json',row)
                rows.append(row)
        progress()
    save(OUTPUT/'execution.json',dict(finished_at=datetime.now(timezone.utc).isoformat(),
        all_tasks_attempted=not pending,attempted=len(rows),pending=pending,stop_reason=stop_reason,
        official_tests_executed=sum(r['official_tests_executed'] for r in rows),tasks=rows))


if __name__=='__main__':main()
