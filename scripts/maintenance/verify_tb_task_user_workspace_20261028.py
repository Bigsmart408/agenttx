"""Zero-inference checks in the original non-root task image and transaction."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(os.environ.get('AGENTTX_PREFLIGHT_PROJECT','/home/bfq/agenttx-workspace/agenttx')).resolve()
sys.path[:0] = [str(PROJECT), str(PROJECT/'src')]
from experiments.scripts.run_tb_dual_local import cell_command

DATA = Path('/mnt/data/agenttx-runtime')
AUDIT = DATA/'validation/tb-task-user-workspace-repair-20261028'


def main():
    active = json.loads((DATA/'validation/active-tb-dual-native.json').read_text())
    registration = json.loads((Path(active['root'])/'registration.json').read_text())
    inventory = json.loads(Path(registration['inventory']).read_text())
    evidence = next(row['evidence'] for row in inventory['official_baseline_tasks']
                    + inventory.get('audited_task_baseline_tasks', [])
                    if row['task'] == 'privilege-escalation')
    tag = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    run = AUDIT/('native-'+tag)
    run.mkdir(parents=True)
    rows = []
    for harness in ('sweagent', 'codex'):
        folder = run/harness
        command = cell_command(PROJECT, folder, dict(task='privilege-escalation',
                               harness=harness, policy='clean_recovery'), evidence)
        command.append('--preflight-only')
        env = dict(os.environ, DOCKER_HOST='unix:///run/agenttx-docker/docker.sock',
                   AGENTTX_DOWNLOAD_PROXY_RETRY='1')
        with (run/(harness+'.log')).open('w') as log:
            process = subprocess.run(command, cwd=PROJECT, env=env, stdout=log,
                                     stderr=subprocess.STDOUT, timeout=240)
        result = json.loads((folder/'clean_recovery/host-result.json').read_text())
        row = dict(harness=harness, returncode=process.returncode,
                   status=result.get('status'), result_path=str(folder/'clean_recovery/host-result.json'),
                   task_user=result.get('original_task_user'),
                   task_user_workspace_preflight=result.get('task_user_workspace_preflight'),
                   no_model_calls=True, official_tests_executed=False,
                   host_forwarding_modified=False, running_workers_modified=False)
        rows.append(row)
        (run/'result.json').write_text(json.dumps(dict(rows=rows,
            returncode=max(r['returncode'] for r in rows), no_model_calls=True,
            official_tests_executed=False, running_workers_modified=False,
            host_forwarding_modified=False), indent=2)+'\n')
        print(json.dumps(row), flush=True)
        assert process.returncode == 0 and result['status'] == 'preflight_passed'
        assert result['original_task_user'] == 'alice'
        assert result['task_user_workspace_preflight']['original_user'] == 'alice'
    print('validation', run/'result.json', flush=True)


if __name__ == '__main__':
    main()
