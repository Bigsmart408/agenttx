"""Install public Torch prerequisites inside a real transaction without network or models."""
import json
from pathlib import Path
import time


def main():
    from agenttx.harness import CodingAgentHarness
    from experiments.scripts.bench_tb_image_native import run_trusted_verifier_tool
    output = Path('/results')
    started = time.monotonic()
    harness = CodingAgentHarness(Path('/app'), session_dir=output/'session', trace_backend='strace')
    try:
        command = ['/tmp/agenttx-uv', 'pip', 'install', '--offline', '--index-url',
                   'https://mirrors.aliyun.com/pypi/simple', '--cache-dir', '/results/cache/uv',
                   '--python', '/usr/bin/python3', '--target', '/tmp/agenttx-dependency-check',
                   'pytest==8.4.1', 'torch==2.7.0', 'transformers==4.55.0']
        installed = run_trusted_verifier_tool(harness.tx, 'public-torch-install', command, 300)
        (output/'install.stdout').write_text(installed.stdout)
        (output/'install.stderr').write_text(installed.stderr)
        assert installed.returncode == 0, installed.stderr
        code = ('import json,torch,transformers,pytest;print(json.dumps(dict('
                'torch=torch.__version__,transformers=transformers.__version__,pytest=pytest.__version__)))')
        imported = run_trusted_verifier_tool(harness.tx, 'public-torch-imports',
            ['env', 'PYTHONPATH=/tmp/agenttx-dependency-check', '/usr/bin/python3', '-c', code], 120)
        assert imported.returncode == 0, imported.stderr
        versions = json.loads(imported.stdout)
        assert versions == dict(torch='2.7.0+cu126', transformers='4.55.0', pytest='8.4.1')
        proof = dict(returncode=0, network='none', no_model_calls=True,
                     transaction_backend='session-strace', versions=versions,
                     install_wall_s=installed.duration_s, wall_s=time.monotonic()-started)
        (output/'native-prerequisite-result.json').write_text(json.dumps(proof, indent=2)+'\n')
        print(json.dumps(proof), flush=True)
    finally:
        harness.close(destroy=False)


if __name__ == '__main__':
    main()
