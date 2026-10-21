#!/usr/bin/env python3
"""Retry Spring's base-image transport timeout before the bounded service build."""
from datetime import datetime,timezone
import json,os
from experiments.scripts.validate_tb_ready_local import DATA,save,digest
from experiments.scripts.validate_tb_sidecars_local import run


def main():
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    output=DATA/'validation/tb-sidecar-spring-base-repair-20261019-r4';output.mkdir(exist_ok=False)
    prior=DATA/'validation/tb-sidecar-repair-20261019-r1/spring-messaging-vul/result.json'
    old=json.loads(prior.read_text())
    assert old['model_calls']==0 and 'TimeoutExpired' in old['error'] and not old['passed']
    save(output/'registration.json',dict(tasks=['spring-messaging-vul'],model_calls=0,gold_solution_used=False,
        prior_result=str(prior),prior_result_sha256=digest(prior),original_results_preserved=True,
        reason='Base image pull exceeded combined 900s build cap; pre-pull with byte/idle deadlines and pin image ID before building.'))
    evidence=json.loads((DATA/'validation/tb-readiness-recheck-20261018.json').read_text())
    record=next(r for r in evidence['checks'] if r['task']=='spring-messaging-vul')
    assert record['identity_verified']
    row=run(record,output)
    save(output/'execution.json',dict(finished_at=datetime.now(timezone.utc).isoformat(),tasks=[row],
        attempted=1,passed=int(row['passed'])))


if __name__=='__main__':main()
