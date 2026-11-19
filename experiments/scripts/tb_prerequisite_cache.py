"""Copy verified public package inputs into a fresh task-private UV cache."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

SEED_ENV = 'AGENTTX_TB_HF_PREREQUISITE_CACHE_PROOF'
VERSIONS = dict(torch='2.7.1+cu126', transformers='4.52.4', peft='0.15.0', psutil='7.0.0', pytest='8.4.1')
PREINSTALL_ENV = 'AGENTTX_TB_HF_PREINSTALL'
TORCH_SEED_ENV = 'AGENTTX_TB_TORCH_PREREQUISITE_CACHE_PROOF'
TORCH_VERSIONS = dict(torch='2.7.0+cu126', transformers='4.55.0', pytest='8.4.1')
TORCH_TASKS = frozenset({'torch-pipeline-parallelism', 'torch-tensor-parallelism'})


def seed_torch_uv_cache(task, output, environment):
    configured = environment.get(TORCH_SEED_ENV)
    if task not in TORCH_TASKS or not configured:
        return None
    proof_path = Path(configured)
    proof = json.loads(proof_path.read_text())
    validation = json.loads((proof_path.parent / 'result-r3.json').read_text())
    versions = json.loads((proof_path.parent / 'imports.json').read_text())
    if (validation.get('returncode') != 0 or validation.get('network') != 'none'
            or validation.get('no_model_calls') is not True
            or validation.get('task_source_mounted') is not False
            or validation.get('official_tests_mounted') is not False):
        raise ValueError('torch cache requires offline public-dependency validation without task inputs')
    if versions != TORCH_VERSIONS:
        raise ValueError('validated torch package versions differ')
    source = Path(proof['cache'])
    if source.is_symlink() or not source.is_dir():
        raise ValueError('package cache must be a real directory')
    expected = (proof['sha256'], proof['regular_files'])
    if cache_digest(source) != expected:
        raise ValueError('torch package cache differs from offline validation')
    target = Path(output) / 'cache/uv'
    if target.exists():
        raise ValueError('never replace an existing task cache')
    target.mkdir(parents=True)
    subprocess.run(['cp', '-a', '--reflink=auto', '--no-preserve=ownership',
                    str(source) + '/.', str(target)], check=True, timeout=600)
    if cache_digest(target) != expected:
        raise ValueError('copied torch package cache failed content verification')
    result = dict(scope='public-fixed-package-inputs-only',
                  contract='torch-2.7.0-transformers-4.55.0-verified-private-cache/v1',
                  versions=versions, cache_sha256=proof['sha256'],
                  regular_files=proof['regular_files'], source=str(source),
                  destination=str(target), independent_copy=True,
                  validation_proof=str(proof_path),
                  validation_proof_sha256=hashlib.sha256(proof_path.read_bytes()).hexdigest(),
                  model_configuration_changed=False, official_tests_changed=False)
    (Path(output) / 'prerequisite-cache.json').write_text(json.dumps(result, indent=2)+'\n')
    return result


def install_fixed_prerequisites(task, output, environment=None):
    """Install only fixed public dependencies before a task transaction/model."""
    environment = os.environ if environment is None else environment
    if task != 'hf-train-lora-adapter' or environment.get(PREINSTALL_ENV) != '1':
        return None
    output = Path(output)
    seed = json.loads((output / 'prerequisite-cache.json').read_text())
    started = time.monotonic()
    command = ['/tmp/agenttx-uv', 'pip', 'install', '--offline', '--system',
               '--index-url', 'https://mirrors.aliyun.com/pypi/simple',
               '--cache-dir', str(output / 'cache/uv'), '--python', 'python3',
               'pytest==8.4.1', 'psutil==7.0.0', 'transformers==4.52.4',
               'torch==2.7.1', 'peft==0.15.0']
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    (output / 'prerequisite-install.stdout.log').write_text(result.stdout)
    (output / 'prerequisite-install.stderr.log').write_text(result.stderr)
    result.check_returncode()
    code = ('import json,torch,transformers,peft,psutil,pytest; '
            'print(json.dumps(dict(torch=torch.__version__,transformers=transformers.__version__,'
            'peft=peft.__version__,psutil=psutil.__version__,pytest=pytest.__version__)))')
    imported = subprocess.run(['python3', '-c', code], capture_output=True, text=True, check=True, timeout=120)
    versions = json.loads(imported.stdout)
    if versions != VERSIONS:
        raise ValueError('task interpreter fixed prerequisite versions differ')
    proof = dict(mode='offline-fixed-global-install/v1', cache_sha256=seed['cache_sha256'],
                 versions=versions, wall_s=time.monotonic()-started,
                 no_model_calls=True, offline=True, before_task_transaction=True)
    (output / 'prerequisite-install.json').write_text(json.dumps(proof, indent=2)+'\n')
    return proof


def cache_digest(root):
    digest = hashlib.sha256()
    count = 0
    for path in sorted(root.rglob('*')):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            digest.update(('L ' + relative + ' ' + os.readlink(path) + '\n').encode())
        elif path.is_file():
            content = hashlib.sha256()
            with path.open('rb') as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                    content.update(chunk)
            digest.update(('F ' + relative + ' ' + content.hexdigest() + '\n').encode())
            count += 1
        elif not path.is_dir():
            raise ValueError('unsupported entry in package cache: ' + relative)
    return digest.hexdigest(), count


def seed_uv_cache(task, output, environment=None):
    environment = os.environ if environment is None else environment
    if task in TORCH_TASKS:
        return seed_torch_uv_cache(task, output, environment)
    configured = environment.get(SEED_ENV)
    if task != 'hf-train-lora-adapter' or not configured:
        return None
    started = time.monotonic()
    proof_path = Path(configured)
    proof = json.loads(proof_path.read_text())
    imports = json.loads((proof_path.parent / 'imports.json').read_text())
    installed = json.loads((proof_path.parent / 'result-r2.json').read_text())
    for validation in (imports, installed):
        if validation.get('returncode') != 0 or validation.get('network') != 'none' or validation.get('no_model_calls') is not True:
            raise ValueError('package cache requires successful offline, zero-model validation')
    versions = json.loads(imports['stdout'])
    if any(versions.get(name) != version for name, version in VERSIONS.items()):
        raise ValueError('validated package versions differ from the recorded prerequisite cache')
    source = Path(proof['cache'])
    if source.is_symlink() or not source.is_dir():
        raise ValueError('package cache must be a real directory')
    expected = (proof['sha256'], proof['regular_files'])
    if cache_digest(source) != expected:
        raise ValueError('package cache content differs from offline validation')
    target = Path(output) / 'cache/uv'
    if target.exists():
        raise ValueError('never replace an existing task cache')
    target.mkdir(parents=True)
    subprocess.run(['cp', '-a', '--reflink=auto', '--no-preserve=ownership',
                    str(source) + '/.', str(target)], check=True, timeout=600)
    if cache_digest(target) != expected:
        raise ValueError('copied package cache failed content verification')
    result = dict(scope='public-fixed-package-inputs-only', versions=VERSIONS,
                  cache_sha256=proof['sha256'], regular_files=proof['regular_files'],
                  source=str(source), destination=str(target), independent_copy=True,
                  validation_proof=str(proof_path),
                  validation_proof_sha256=hashlib.sha256(proof_path.read_bytes()).hexdigest(),
                  wall_s=time.monotonic() - started,
                  model_configuration_changed=False, official_tests_changed=False)
    (Path(output) / 'prerequisite-cache.json').write_text(json.dumps(result, indent=2) + '\n')
    return result
