#!/usr/bin/env python3
"""Retry only four audited installer timeouts with corrected index precedence."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import datetime,timezone
import json,os
from experiments.scripts.validate_tb_ready_local import DATA,save,digest
from experiments.scripts.validate_tb_bootstrap_batch import run

TASKS=['pytorch-model-cli','torch-pipeline-parallelism','torch-tensor-parallelism','triton-interpret']


def main():
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    output=DATA/'validation/tb-bootstrap-index-repair-20261019-r6';output.mkdir(exist_ok=False)
    proof=[]
    for task in TASKS:
        folder=DATA/'validation/tb-bootstrap-repair-20261019-r1'/task
        prior=json.loads((folder/'result.json').read_text())
        text=(folder/'baseline.stderr').read_text()
        assert prior['model_calls']==0 and prior['baseline_classification']=='timeout'
        assert 'Downloading ' in text and '+ uv run pytest' not in text
        proof.append(dict(task=task,result_sha256=digest(folder/'result.json'),
            installer_log_sha256=digest(folder/'baseline.stderr'),reason='Timed out during dependency download before pytest.'))
    save(output/'registration.json',dict(tasks=TASKS,workers=2,timeout_s=1800,model_calls=0,
        gold_solution_used=False,previous_failures=proof,original_results_preserved=True,
        index_rule='UV_INDEX_URL sets domestic default; unset UV_DEFAULT_INDEX so explicit upstream --index-url wins.',
        task_outcomes_retried=False))
    evidence=json.loads((DATA/'validation/tb-readiness-recheck-20261018.json').read_text())
    records=[r for r in evidence['checks'] if r['task'] in TASKS and r['identity_verified']]
    assert len(records)==len(TASKS)
    rows=[]
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(run,r,output,1800,'https://mirrors.aliyun.com/pypi/simple') for r in records]
        for future in as_completed(futures):
            rows.append(future.result());save(output/'progress.json',dict(completed=len(rows),total=len(TASKS),tasks=rows))
    save(output/'execution.json',dict(finished_at=datetime.now(timezone.utc).isoformat(),tasks=rows,
        attempted=len(rows),official_tests_executed=sum(r['official_tests_executed'] for r in rows)))


if __name__=='__main__':main()
