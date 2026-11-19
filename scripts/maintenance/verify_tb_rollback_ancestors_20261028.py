"""Zero-inference original-user workspace checks after every real policy."""
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(os.environ['AGENTTX_PREFLIGHT_PROJECT']).resolve()
sys.path[:0] = [str(PROJECT), str(PROJECT/'src')]
from experiments.scripts.run_tb_dual_local import cell_command
from experiments.scripts.bench_tb_image_native import POLICIES

DATA = Path('/mnt/data/agenttx-runtime')
AUDIT = Path(os.environ.get('AGENTTX_PREFLIGHT_AUDIT',
                           str(DATA/'validation/tb-rollback-ancestor-repair-20261028')))


def main():
    pointer = json.loads((DATA/'validation/active-tb-dual-native.json').read_text())
    registration = json.loads((Path(pointer['root'])/'registration.json').read_text())
    inventory = json.loads(Path(registration['inventory']).read_text())
    evidence = next(r['evidence'] for r in inventory['official_baseline_tasks']
                    + inventory.get('audited_task_baseline_tasks', [])
                    if r['task'] == 'privilege-escalation')
    tag = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run = AUDIT/('native-'+tag)
    run.mkdir(parents=True)

    def check(item):
        harness, policy = item
        folder = run/harness/policy
        folder.parent.mkdir(parents=True, exist_ok=True)
        command = cell_command(PROJECT, folder, dict(task='privilege-escalation',
                               harness=harness, policy=policy), evidence)
        command.append('--preflight-only')
        env = dict(os.environ, DOCKER_HOST='unix:///run/agenttx-docker/docker.sock',
                   AGENTTX_DOWNLOAD_PROXY_RETRY='1')
        with (folder.parent/(policy+'.driver.log')).open('w') as log:
            process = subprocess.run(command, cwd=PROJECT, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=240)
        path = folder/policy/'host-result.json'
        result = json.loads(path.read_text())
        return dict(harness=harness, policy=policy, returncode=process.returncode,
                    status=result.get('status'), result_path=str(path),
                    error=result.get('error'), task_user=result.get('original_task_user'),
                    task_user_codex_profile=result.get('task_user_codex_profile'),
                    task_user_workspace_preflight=result.get('task_user_workspace_preflight'))

    rows = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(check, (h, p)) for h in ('sweagent', 'codex') for p in POLICIES]
        for future in concurrent.futures.as_completed(jobs):
            row = future.result()
            rows.append(row)
            passed = all(r['returncode'] == 0 and r['status'] == 'preflight_passed'
                         and r['task_user'] == 'alice'
                         and r['task_user_workspace_preflight']['workspace_writable_returncode'] == 0
                         and (r['harness'] != 'codex' or
                              r['task_user_workspace_preflight'].get('private_profile_readable') is True)
                         for r in rows)
            (run/'result.json').write_text(json.dumps(dict(rows=rows,
                returncode=0 if passed else 1, complete=len(rows)==12,
                stage=str(PROJECT), no_model_calls=True, official_tests_executed=False,
                running_workers_modified=False, host_forwarding_modified=False), indent=2)+'\n')
            print(json.dumps(row), flush=True)
    print('validation', run/'result.json', flush=True)
    assert passed and len(rows) == 12


if __name__ == '__main__':
    main()
