"""Preserve evidence and stop one Clean job that bypassed its original deadline."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    data = Path('/mnt/data/agenttx-runtime')
    audit = data/'validation/tb-swe-clean-deadline-repair-20261028'
    audit.mkdir(exist_ok=True)
    assert not (audit/'signal-start.json').exists()
    root = Path(json.loads((data/'validation/active-tb-dual-native.json').read_text())['root'])
    progress = json.loads((root/'progress.json').read_text())
    rows = progress['tasks'] + progress['retry_tasks']
    row = next(r for r in rows if r['state'] == 'running' and
               (r['task'], r['harness'], r['policy']) ==
               ('privilege-escalation', 'sweagent', 'clean_recovery'))
    folder = Path(row['result_path']).parent
    command = json.loads((folder/'container-command.json').read_text())
    assert float(command[command.index('--timeout')+1]) == 3600
    assert float(command[command.index('--verifier-timeout')+1]) == 1800
    assert not (folder/'model-result.json').exists()
    assert not Path(row['result_path']).exists()
    env = dict(os.environ, DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    ids = subprocess.check_output(['docker', 'ps', '-q'], env=env, text=True).split()
    containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids], env=env, text=True))
    container = next(c for c in containers if any(
        m['Source'] == str(folder) for m in c['Mounts']))
    assert container['HostConfig']['NetworkMode'] != 'host'
    frozen = Path(next(m['Source'] for m in container['Mounts']
                       if m['Destination'].startswith('/home/bfq/agenttx-workspace/agenttx-executions/')
                       and m['Destination'].endswith('/project')))
    source = frozen/'experiments/scripts/sweagent_native_workload.py'
    text = source.read_text()
    assert 'result = adapter.run(' in text
    assert 'step_limit = 1 if clean_baseline else self.max_steps' not in text
    birth = subprocess.check_output(
        ['stat', '-c', '%w', str(folder/'model/trajectories')], text=True).strip()
    setup = datetime.datetime.fromisoformat(birth)
    now = datetime.datetime.now(datetime.timezone.utc)
    elapsed = (now-setup).total_seconds()
    # This directory is created by adapter.setup after the timer is armed.
    assert elapsed > 3660
    proof = dict(
        at=now.isoformat(), row=row, container=container['Id'],
        private_network=container['HostConfig']['NetworkMode'],
        frozen_source=str(frozen), source_sha256=sha(source),
        registered_model_timeout_s=3600, registered_verifier_timeout_s=1800,
        trajectory_directory_birth_utc=setup.isoformat(),
        elapsed_since_setup_s=elapsed, timer_started_before_adapter_setup=True,
        host_forwarding_modified=False,
        other_running_workers=[r for r in rows if r['state'] == 'running'
                               and r['result_path'] != row['result_path']],
        history=[dict(result_path=r['result_path'], sha256=sha(r['result_path']))
                 for r in rows if r['state'] in ('completed', 'infrastructure_error')],
        signal='SIGINT', signal_target='exact container native coordinator PID 1',
    )
    shutil.copyfile(folder/'container.stdout.log', audit/'before-signal.stdout.log')
    proof['log_sha256'] = sha(audit/'before-signal.stdout.log')
    observation = audit/'readonly-observation.json'
    assert not observation.exists()
    observation.write_text(json.dumps(proof, indent=2)+'\n')
    result = subprocess.run(
        ['docker', 'exec', container['Id'], 'python3', '-c',
         'import os,signal; os.kill(1,signal.SIGINT)'],
        env=env, text=True, capture_output=True, timeout=15)
    (audit/'signal-start.json').write_text(json.dumps(dict(
        at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        observation_path=str(observation), observation_sha256=sha(observation),
        returncode=result.returncode, stdout=result.stdout, stderr=result.stderr,
    ), indent=2)+'\n')
    print(json.dumps(dict(returncode=result.returncode, container=container['Id'],
                         original_model_limit_s=3600, elapsed_since_setup_s=elapsed,
                         affected_workers=1, other_workers_signalled=0, audit=str(audit))))


if __name__ == '__main__':
    main()
