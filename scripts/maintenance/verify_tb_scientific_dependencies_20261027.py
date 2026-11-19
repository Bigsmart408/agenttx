"""Verify collected exact dependencies in the original image with no network.

This prerequisite probe never evaluates a candidate answer or modifies workers.
"""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess


BASE = Path('/mnt/data/agenttx-runtime/validation/tb-scientific-stack-dependency-20261027')
IMAGE = 'sha256:1537171daf852dcda691f0ce4d7ecaafe9ac0622757ec70475898a66c4cc1bb9'
UV = Path('/mnt/data/agenttx-runtime/validation/tb-validation-tools-20261018/bin/uv')
CHECK = r'''set -eu
/probe/uv venv --python python3 /probe-output/venv
/probe/uv pip install --python /probe-output/venv/bin/python --no-index --find-links /wheelhouse -r /probe/requirements.txt
/probe/uv pip check --python /probe-output/venv/bin/python
/probe-output/venv/bin/python - <<'PY'
import importlib.metadata, json
from pathlib import Path
import numpy as np
import pandas as pd
import scipy
from scipy import linalg, optimize
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

versions = {}
for line in Path('/probe/requirements.txt').read_text().splitlines():
    package, expected = line.split('==')
    actual = importlib.metadata.version(package)
    assert actual == expected, (package, expected, actual)
    versions[package] = actual
a = np.array([[3., 1.], [1., 2.]])
b = np.array([9., 8.])
assert np.allclose(a @ linalg.solve(a, b), b)
assert optimize.minimize_scalar(lambda x: (x - 3.) ** 2).success
assert pd.DataFrame({'x': [1, 2, 3]}).x.sum() == 6
plt.plot([0, 1], [0, 1])
plt.savefig('/probe-output/import-smoke.png')
print(json.dumps({'versions': versions, 'numpy_scipy_pandas_matplotlib_smoke': 'passed'}))
PY
'''


def main():
    manifest = json.loads((BASE / 'official-collection-result.json').read_text())
    requirements = (BASE / 'requirements-observed.txt').read_text().splitlines()
    rows = manifest['rows']
    assert manifest['required_count'] == manifest['verified_count'] == len(requirements) == 19
    assert {r['package'] for r in rows} == set(requirements)
    for row in rows:
        wheel = BASE / 'verified-wheels' / row['filename']
        assert row['verified'] and wheel.stat().st_size == row['bytes']
        assert hashlib.sha256(wheel.read_bytes()).hexdigest() == row['sha256']
    output = BASE / 'offline-probe'
    output.mkdir(exist_ok=True)
    assert not (output / 'venv').exists(), 'Use a fresh probe output directory'
    script = BASE / 'offline-prerequisite-check.sh'
    script.write_text(CHECK)
    name = 'agenttx-scientific-prerequisite-probe-20261027'
    env = dict(os.environ, DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    args = ['docker', 'run', '--rm', '--name', name, '--network', 'none',
            '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges',
            '--pids-limit', '256', '--memory', '2g', '--cpus', '2',
            '--user', f'{os.getuid()}:{os.getgid()}', '--workdir', '/tmp',
            '-e', 'UV_CACHE_DIR=/probe-output/uv-cache',
            '-e', 'UV_PYTHON_DOWNLOADS=never', '-e', 'UV_LINK_MODE=copy',
            '-e', 'MPLCONFIGDIR=/probe-output/matplotlib',
            '--mount', f'type=bind,src={BASE}/verified-wheels,dst=/wheelhouse,readonly',
            '--mount', f'type=bind,src={BASE}/requirements-observed.txt,dst=/probe/requirements.txt,readonly',
            '--mount', f'type=bind,src={script},dst=/probe/check.sh,readonly',
            '--mount', f'type=bind,src={UV},dst=/probe/uv,readonly',
            '--mount', f'type=bind,src={output},dst=/probe-output',
            '--entrypoint', 'sh', IMAGE, '/probe/check.sh']
    at = datetime.datetime.now(datetime.timezone.utc).isoformat()
    try:
        result = subprocess.run(args, env=env, text=True, capture_output=True, timeout=180)
        rc, stdout, stderr = result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired as exc:
        rc = 124
        stdout = (exc.stdout or b'').decode(errors='replace')
        stderr = (exc.stderr or b'').decode(errors='replace')
        subprocess.run(['docker', 'rm', '-f', name], env=env, capture_output=True, timeout=20)
    (BASE / 'offline-probe.stdout').write_text(stdout)
    (BASE / 'offline-probe.stderr').write_text(stderr)
    proof = dict(at=at, finished_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                 returncode=rc, image_id=IMAGE, network='none', exact_versions=19,
                 official_hashes_verified=True, fresh_venv=True,
                 inherits_system_site_packages=False, official_task_tests_run=False,
                 candidate_outputs_evaluated=False, no_model_calls=True,
                 running_workers_modified=False, host_forwarding_modified=False,
                 deployed_to_experiments=False)
    (BASE / 'offline-prerequisite-result.json').write_text(json.dumps(proof, indent=2) + '\n')
    print(json.dumps(proof), flush=True)
    print(stdout[-1800:])
    print(stderr[-1800:])
    raise SystemExit(rc)


if __name__ == '__main__':
    main()
