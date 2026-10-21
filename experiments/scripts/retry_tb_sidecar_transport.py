#!/usr/bin/env python3
"""Repeat only documented short-deadline image-preparation failures with byte monitoring."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timezone
import hashlib,json,time
from pathlib import Path
from experiments.scripts.validate_tb_ready_local import DATA,save
from experiments.scripts.validate_tb_sidecars_local import run


def main():
    old=DATA/'validation/tb-sidecar-repair-20261019-r1'
    output=DATA/'validation/tb-sidecar-byte-repairs-20261019-r3'
    output.mkdir(exist_ok=False)
    evidence=json.loads((DATA/'validation/tb-readiness-recheck-20261018.json').read_text())
    images={r['task']:r for r in evidence['checks'] if r['identity_verified']}
    registration=dict(tasks=[],dynamic_total=True,purpose='Retry only 300s image pull diagnostic failures with registered byte-progress deadlines.',
        model_calls=0,gold_solution_used=False,original_results_preserved=True,already_repairing=['security-celery-redis-rce'])
    save(output/'registration.json',registration)
    seen={'security-celery-redis-rce'};active={};rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        while True:
            for path in old.glob('*/result.json'):
                task=path.parent.name
                if task in seen:continue
                seen.add(task);prior=json.loads(path.read_text())
                if prior.get('passed') or not str(prior.get('error','')).startswith("RuntimeError('All domestic sources failed for "):continue
                registration['tasks'].append(task)
                save(output/'registration.json',registration)
                save(output/(task+'.prior-proof.json'),dict(task=task,source=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                    original_error=prior['error'],environment_only=True,model_calls=0))
                active[pool.submit(run,images[task],output)]=task
            for future in list(active):
                if future.done():
                    rows.append(future.result());del active[future]
                    save(output/'progress.json',dict(completed=len(rows),total=len(registration['tasks']),tasks=rows))
            if (old/'execution.json').exists() and not active:break
            time.sleep(10)
    save(output/'execution.json',dict(finished_at=datetime.now(timezone.utc).isoformat(),attempted=len(rows),
        passed=sum(r['passed'] for r in rows),tasks=rows))

if __name__=='__main__':main()
