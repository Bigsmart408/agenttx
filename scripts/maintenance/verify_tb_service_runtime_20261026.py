"""Run real namespace regressions locally, without network or model calls."""
import argparse
import datetime
import json
import os
from pathlib import Path
import subprocess

ROOT = Path('/home/bfq/agenttx-workspace/agenttx')
DATA = Path('/mnt/data/agenttx-runtime')
IMAGE = 'sha256:10b423d23527e36da72c554fa82426553a0a8474926e5dcc80048dda358baf0c'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--name', required=True)
    parser.add_argument('--deselect', action='append', default=[])
    parser.add_argument('tests', nargs='+')
    args = parser.parse_args()
    folder = DATA/'validation/tb-socket-service-repair-20261026'/args.name
    folder.mkdir(parents=True, exist_ok=False)
    command = ['docker', 'run', '--rm', '--privileged', '--network', 'none', '--tmpfs', '/tmp:exec',
               '--pull', 'never', '--entrypoint', '/usr/bin/python3']
    for source, target in [
        (ROOT, ROOT), (Path('/home/bfq/.venvs/agenttx'), Path('/home/bfq/.venvs/agenttx')),
        (Path('/home/bfq/.local/share/uv/python'), Path('/home/bfq/.local/share/uv/python')),
        (DATA/'validation/tb-validation-tools-20261018/static-strace/strace-6.13+ds/src/strace', Path('/usr/bin/strace')),
    ]:
        command += ['--mount', f'type=bind,src={source},dst={target},readonly']
    command += ['--mount', f'type=bind,src={folder},dst=/results', '--workdir', str(ROOT),
                '-e', f'PYTHONPATH={ROOT}/src', '-e', 'PYTHONDONTWRITEBYTECODE=1',
                '-e', 'TRY_BIND_READONLY=/usr/bin/strace', IMAGE, '-c',
                'import subprocess,sys; p=subprocess.run(sys.argv[1:],timeout=150); raise SystemExit(p.returncode)',
                '/home/bfq/.venvs/agenttx/bin/python', '-m', 'pytest', *args.tests,
                '-x', '-vv', '-p', 'no:cacheprovider', '--basetemp=/tmp/agenttx-runtime-pytest']
    command += ['--deselect='+name for name in args.deselect]
    with (folder/'pytest.log').open('w') as out:
        result = subprocess.run(command, env={**os.environ, 'DOCKER_HOST': 'unix:///run/agenttx-docker/docker.sock'},
                                stdout=out, stderr=subprocess.STDOUT, timeout=180)
    proof = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), tests=args.tests,
                 returncode=result.returncode, no_network=True, no_model_calls=True,
                 log=str(folder/'pytest.log'))
    (folder/'result.json').write_text(json.dumps(proof, indent=2)+'\n')
    print(json.dumps(proof))
    print((folder/'pytest.log').read_text(errors='replace')[-5000:])
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
