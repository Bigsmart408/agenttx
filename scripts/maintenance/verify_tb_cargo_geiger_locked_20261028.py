"""Validate the official geiger version with its published lockfile, without models."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess

DATA = Path('/mnt/data/agenttx-runtime')
AUDIT = DATA / 'validation/tb-cargo-geiger-lock-20261028'


def main():
    active = json.loads((DATA / 'validation/active-tb-dual-native.json').read_text())
    progress = json.loads((Path(active['root']) / 'progress.json').read_text())
    source = next(row for row in progress['tasks'] + progress['retry_tasks']
                  if row['task'] == 'port-compressor' and row['state'] == 'completed')
    result_path = Path(source['result_path'])
    image = json.loads(result_path.read_text())['image_id']
    live = next(row for row in progress['tasks'] + progress['retry_tasks']
                if row['task'] == 'polyglot-c-py' and row['harness'] == 'sweagent'
                and row['policy'] == 'causal' and row['state'] == 'running')
    network = json.loads((Path(live['result_path']).parent /
                          'task-services/download-proxy.json').read_text())['network']
    assert network.startswith('container:')
    now = datetime.datetime.now(datetime.timezone.utc)
    folder = AUDIT / ('isolated-install-' + now.strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(parents=True)
    # The image runs as root with all capabilities dropped. This isolated,
    # credential-free artifact directory must therefore permit that UID.
    folder.chmod(0o777)
    shell = r'''set -eu
export CARGO_HOME=/probe/cargo-home CARGO_TARGET_DIR=/probe/target
export PATH="$CARGO_HOME/bin:$PATH"
rustc --version
cargo --version
cargo install cargo-geiger --version 0.12.0 --locked --jobs 2
cargo geiger --version
cargo new --edition 2021 /probe/safe-probe
cd /probe/safe-probe
cargo geiger --output-format Json > /probe/geiger-probe.json
python3 -c 'import json; x=json.load(open("/probe/geiger-probe.json")); assert isinstance(x.get("packages"),list) and x["packages"]; print("geiger JSON probe passed")'
'''
    from experiments.scripts.tb_download_proxy import environment
    command = ['docker', 'run', '--rm', '--pull=never', '--cap-drop=ALL',
               '--security-opt=no-new-privileges', '--network=' + network,
               '--cidfile', str(folder / 'container.cid'),
               '--mount', 'type=bind,src=' + str(folder) + ',dst=/probe']
    for key, value in environment().items():
        command.extend(['-e', key + '=' + value])
    command.extend(['--entrypoint', '/bin/bash', image, '-lc', shell])
    (folder / 'command.json').write_text(json.dumps(command, indent=2) + '\n')
    print('probe_folder', folder, flush=True)
    env = dict(os.environ, DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    try:
        with (folder / 'stdout.log').open('w') as stdout, (folder / 'stderr.log').open('w') as stderr:
            run = subprocess.run(command, env=env, stdout=stdout, stderr=stderr, timeout=1200)
        rc = run.returncode
    except subprocess.TimeoutExpired:
        rc = 124
    finally:
        cidfile = folder / 'container.cid'
        if cidfile.is_file():
            subprocess.run(['docker', 'rm', '-f', cidfile.read_text().strip()], env=env,
                           capture_output=True, timeout=20)
    proof = dict(started_at=now.isoformat(),
                 finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 returncode=rc, original_image=image, no_model_calls=True,
                 official_tests_executed=False, running_workers_modified=False,
                 host_forwarding_modified=False, no_privileged_container=True,
                 network='existing_private_task_namespace',
                 original_result_path=str(result_path),
                 original_result_sha256=hashlib.sha256(result_path.read_bytes()).hexdigest(),
                 version='0.12.0', change='published lockfile only',
                 deployed_to_experiments=False)
    (folder / 'result.json').write_text(json.dumps(proof, indent=2) + '\n')
    print(json.dumps(proof), flush=True)
    raise SystemExit(rc)


if __name__ == '__main__':
    main()
