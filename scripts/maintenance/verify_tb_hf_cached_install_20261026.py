"""Verify fixed HF prerequisites in the real task transaction; no model calls."""
import json
import os
from pathlib import Path
import time


def main():
    from agenttx.harness import CodingAgentHarness
    from experiments.scripts.bench_tb_image_native import run_trusted_verifier_tool
    output = Path('/results')
    started = time.monotonic()
    harness = CodingAgentHarness(Path('/app'), session_dir=output/'session', trace_backend='strace')
    try:
        argv = ['/uv-prerequisite', 'pip', 'install', '--offline', '--index-url',
                'https://mirrors.aliyun.com/pypi/simple', '--cache-dir', '/results/cache/uv',
                '--python', '/usr/local/bin/python3', '--target', '/tmp/agenttx-prerequisite-check',
                'pytest==8.4.1', 'psutil==7.0.0', 'transformers==4.52.4', 'torch==2.7.1', 'peft==0.15.0']
        result = run_trusted_verifier_tool(harness.tx, 'fixed-prerequisite-install', argv, 900)
        (output/'install.stdout').write_text(result.stdout)
        (output/'install.stderr').write_text(result.stderr)
        if result.returncode:
            raise RuntimeError('fixed dependency install failed')
        code = 'import json,torch,transformers,peft,pytest,psutil; print(json.dumps(dict(torch=torch.__version__,transformers=transformers.__version__,peft=peft.__version__,pytest=pytest.__version__,psutil=psutil.__version__)))'
        imported = run_trusted_verifier_tool(harness.tx, 'fixed-prerequisite-imports',
            ['env', 'PYTHONPATH=/tmp/agenttx-prerequisite-check', '/usr/local/bin/python3', '-c', code], 120)
        (output/'imports.stdout').write_text(imported.stdout)
        (output/'imports.stderr').write_text(imported.stderr)
        assert imported.returncode == 0, imported.stderr
        proof = dict(returncode=0, wall_s=time.monotonic()-started, no_model_calls=True,
                     network='none', transaction_backend='session-strace',
                     versions=json.loads(imported.stdout), install_wall_s=result.duration_s)
        (output/'native-prerequisite-result.json').write_text(json.dumps(proof, indent=2)+'\n')
        print(json.dumps(proof), flush=True)
    finally:
        harness.close(destroy=False)


if __name__ == '__main__':
    main()
