"""Exercise the repaired verifier shell inside an isolated native transaction."""
import datetime
import json
import os
from pathlib import Path
import subprocess

DATA = Path('/mnt/data/agenttx-runtime')
AUDIT = DATA / 'validation/tb-cargo-geiger-lock-20261028'
PROJECT = Path('/home/bfq/agenttx-workspace/agenttx').resolve()


def main():
    installs = sorted(AUDIT.glob('isolated-install-*/result.json'))
    install_result = installs[-1]
    assert json.loads(install_result.read_text())['returncode'] == 0
    installed = install_result.parent
    active = json.loads((DATA / 'validation/active-tb-dual-native.json').read_text())
    progress = json.loads((Path(active['root']) / 'progress.json').read_text())
    original = next(row for row in progress['tasks'] if row['task'] == 'port-compressor'
                    and row['harness'] == 'sweagent' and row['policy'] == 'crash_no_rollback')
    source = Path(original['result_path']).parent
    live = next(row for row in progress['tasks'] if row['task'] == 'polyglot-c-py'
                and row['harness'] == 'sweagent' and row['policy'] == 'causal'
                and row['state'] == 'running')
    network = json.loads((Path(live['result_path']).parent /
                          'task-services/download-proxy.json').read_text())['network']
    folder = AUDIT / ('native-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir()
    command = json.loads((source / 'container-command.json').read_text())
    command = command[:command.index('--entrypoint') + 3]
    command[command.index('--network') + 1] = network
    command[command.index('--cidfile') + 1] = str(folder / 'container.cid')
    frozen = json.loads((Path(active['root']) / 'registration.json').read_text())['frozen_source']
    new_command = []
    options = iter(command)
    for value in options:
        if value == '--mount':
            mount = next(options)
            if ',dst=/home/bfq/.config/agenttx/experiment-20261017,' in mount:
                continue
            new_command.append(value)
            value = mount
        if value.startswith('type=bind,'):
            value = value.replace('src=' + str(source) + ',dst=/results',
                                  'src=' + str(folder) + ',dst=/results')
            value = value.replace('src=' + frozen + ',', 'src=' + str(PROJECT) + ',')
        if value.startswith('TRY_BIND_READONLY='):
            paths = value.split('=', 1)[1].split(':')
            paths = [p for p in paths if p != '/home/bfq/.config/agenttx/experiment-20261017']
            value = 'TRY_BIND_READONLY=' + ':'.join(paths + ['/locked-geiger-artifacts'])
        new_command.append(value)
    command = new_command
    at = command.index('--entrypoint')
    command[at:at] = ['--mount', 'type=bind,src=' + str(installed) + ',dst=/locked-geiger-artifacts,readonly']
    shell = r'''set -eu
mkdir -p /tmp/geiger-cargo-home/bin
cp /locked-geiger-artifacts/cargo-home/bin/cargo-geiger /tmp/geiger-cargo-home/bin/
cp /locked-geiger-artifacts/cargo-home/.crates* /tmp/geiger-cargo-home/
ln -s /locked-geiger-artifacts/cargo-home/registry /tmp/geiger-cargo-home/registry
export CARGO_HOME=/tmp/geiger-cargo-home
export PATH="$CARGO_HOME/bin:$PATH"
export CARGO_TARGET_DIR=/tmp/geiger-target CARGO_NET_OFFLINE=true
cargo install cargo-geiger --version 0.12.0
cargo geiger --version
cp -r /locked-geiger-artifacts/safe-probe /tmp/geiger-safe-probe
cd /tmp/geiger-safe-probe
cargo geiger --output-format Json > /tmp/geiger-result.json
python3 -c 'import json; x=json.load(open("/tmp/geiger-result.json")); assert isinstance(x.get("packages"),list) and x["packages"]; print("native geiger JSON probe passed")'
'''
    probe = '\n'.join([
        'import json,pathlib,time',
        'from experiments.scripts.bench_tb_image_native import prepare_runtime_tools,prime_resolver',
        'from experiments.scripts.tb_verifier_cargo_lock import SHELL,CONTRACT',
        'from agenttx.harness import CodingAgentHarness',
        'from agenttx.policy import CommitPolicy',
        "prepare_runtime_tools(pathlib.Path('/results'))",
        "h=CodingAgentHarness(pathlib.Path('/app'),session_dir=pathlib.Path('/results/session'),trace_backend='strace',policy=CommitPolicy(pathlib.Path('/app'),allow_external_writes=True,ephemeral_roots=(pathlib.Path('/root'),pathlib.Path('/tmp'))))",
        'prime_resolver(h)',
        'start=time.monotonic()',
        "r=h.tx.run_tool('locked-geiger-verifier-prerequisite',['bash','-c',SHELL+" + repr(shell) + "],trace_reads=False,timeout_s=180)",
        'result=dict(returncode=r.returncode,seconds=time.monotonic()-start,stdout=r.stdout,stderr=r.stderr,contract=CONTRACT)',
        "pathlib.Path('/results/native-install-result.json').write_text(json.dumps(result,indent=2)+chr(10))",
        'print(json.dumps(result))',
        'h.close(destroy=False)',
        'raise SystemExit(r.returncode)',
    ])
    compile(probe, 'geiger-native-probe', 'exec')
    command.extend(['-c', probe])
    print('native_probe_folder', folder, flush=True)
    env = dict(os.environ, DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    try:
        with (folder / 'stdout.log').open('w') as stdout, (folder / 'stderr.log').open('w') as stderr:
            run = subprocess.run(command, env=env, stdout=stdout, stderr=stderr, timeout=240)
        rc = run.returncode
    except subprocess.TimeoutExpired:
        rc = 124
    finally:
        cidfile = folder / 'container.cid'
        if cidfile.is_file():
            subprocess.run(['docker', 'rm', '-f', cidfile.read_text().strip()], env=env,
                           capture_output=True, timeout=20)
    result = dict(returncode=rc, no_model_calls=True, official_tests_executed=False,
                  published_lock_install=str(install_result), running_workers_modified=False,
                  host_forwarding_modified=False, source_mount_replaced=True,
                  original_result_mount_replaced=True, deployed_to_experiments=False)
    (folder / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    raise SystemExit(rc)


if __name__ == '__main__':
    main()
