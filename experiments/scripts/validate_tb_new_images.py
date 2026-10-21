#!/usr/bin/env python3
"""Validate newly built TB images with the original, model-free bootstrap."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import traceback

from experiments.scripts.validate_tb_bootstrap_batch import run
from experiments.scripts.validate_tb_ready_local import DATA, digest, save
from experiments.workloads import terminal_bench_suite as tb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-progress', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tasks', nargs='+', required=True)
    parser.add_argument('--github-proxy', choices=['https://gh-proxy.com/', 'https://ghfast.top/'])
    args = parser.parse_args()
    assert socket.gethostname() == 'r760-ssd'
    assert os.environ['DOCKER_HOST'] == 'unix:///run/agenttx-docker/docker.sock'
    assert args.output.resolve().is_relative_to(DATA/'validation')
    assert len(set(args.tasks)) == len(args.tasks)
    built = {r['task']: r for r in json.loads(args.build_progress.read_text())['tasks']}
    tasks = tb.load_tasks(DATA/'cache')
    records = []
    for name in args.tasks:
        row = built[name]
        assert row['state'] == 'image_ready'
        source = tb.task_source(DATA/'cache', tasks[name])
        assert digest(tb._docker_build_paths(source)[1]) == row['original_dockerfile_sha256']
        info = json.loads(subprocess.check_output(
            ['docker', 'image', 'inspect', row['image_tag']], text=True, timeout=30))[0]
        assert info['Id'] == row['image_id']
        assert info['Architecture'] == 'amd64' and info['Os'] == 'linux'
        records.append(dict(task=name, local_image_id=info['Id']))
    assert shutil.disk_usage(DATA).free >= 350*2**30
    args.output.mkdir(exist_ok=False)
    save(args.output/'registration.json', dict(
        registered_at=datetime.now(timezone.utc).isoformat(), tasks=args.tasks,
        build_evidence=str(args.build_progress), build_evidence_sha256=digest(args.build_progress),
        records=records, workers=2, timeout_s=900, model_calls=0, gold_solution_used=False,
        source_sha256=digest(Path(__file__)), bootstrap_source_sha256=digest(Path(__file__).with_name('validate_tb_bootstrap_batch.py')),
        original_image_identity_verified=True, github_proxy=args.github_proxy,
        purpose='Original verifier bootstrap only; disposable unsolved workspaces; no model retries.'))
    results = []
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = {pool.submit(run, row, args.output, 900,
                'https://mirrors.aliyun.com/pypi/simple',args.github_proxy): row['task'] for row in records}
        for future in as_completed(jobs):
            name = jobs[future]
            try:
                result = future.result()
            except Exception as exc:
                folder = args.output/name
                folder.mkdir(exist_ok=True)
                (folder/'coordinator-error.txt').write_text(traceback.format_exc())
                result = dict(task=name, official_tests_executed=False,
                    baseline_classification='environment_preparation_error', error=repr(exc), model_calls=0)
                save(folder/'result.json', result)
            results.append(result)
            save(args.output/'progress.json', dict(
                updated_at=datetime.now(timezone.utc).isoformat(), completed=len(results),
                total=len(records), tasks=results))
    save(args.output/'execution.json', dict(finished_at=datetime.now(timezone.utc).isoformat(),
        attempted=len(results), official_tests_executed=sum(r['official_tests_executed'] for r in results),
        tasks=results))


if __name__ == '__main__':
    main()
