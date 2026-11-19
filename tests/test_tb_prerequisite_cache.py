import json

import pytest

from experiments.scripts.tb_prerequisite_cache import SEED_ENV, VERSIONS, cache_digest, seed_uv_cache


def validated_cache(tmp_path):
    source = tmp_path / 'seed'
    source.mkdir()
    (source / 'package.whl').write_bytes(b'verified public package')
    (source / 'relative').symlink_to('package.whl')
    digest, count = cache_digest(source)
    proof = tmp_path / 'cache-integrity.json'
    proof.write_text(json.dumps(dict(cache=str(source), sha256=digest, regular_files=count)))
    validation = dict(returncode=0, network='none', no_model_calls=True)
    (tmp_path / 'result-r2.json').write_text(json.dumps(validation))
    (tmp_path / 'imports.json').write_text(json.dumps(dict(validation, stdout=json.dumps(VERSIONS))))
    return source, {SEED_ENV: str(proof)}


def test_copy_is_verified_and_cannot_modify_source_cache(tmp_path):
    source, env = validated_cache(tmp_path)
    output = tmp_path / 'attempt'
    result = seed_uv_cache('hf-train-lora-adapter', output, env)
    copied = output / 'cache/uv/package.whl'
    assert result['cache_sha256'] == cache_digest(source)[0]
    assert copied.stat().st_ino != (source / 'package.whl').stat().st_ino
    copied.write_bytes(b'task changed its private cache')
    assert (source / 'package.whl').read_bytes() == b'verified public package'
    assert (output / 'cache/uv/relative').readlink().as_posix() == 'package.whl'
    with pytest.raises(ValueError, match='never replace'):
        seed_uv_cache('hf-train-lora-adapter', output, env)


def test_changed_seed_fails_before_launch(tmp_path):
    source, env = validated_cache(tmp_path)
    (source / 'package.whl').write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='differs from offline'):
        seed_uv_cache('hf-train-lora-adapter', tmp_path / 'attempt', env)
    assert not (tmp_path / 'attempt').exists()


def test_other_tasks_do_not_use_hf_cache(tmp_path):
    assert seed_uv_cache('other-task', tmp_path / 'attempt', {SEED_ENV: '/missing'}) is None
    assert seed_uv_cache('hf-train-lora-adapter', tmp_path / 'attempt', {}) is None
    assert not (tmp_path / 'attempt').exists()


def validated_torch_cache(tmp_path):
    from experiments.scripts.tb_prerequisite_cache import TORCH_SEED_ENV, TORCH_VERSIONS
    source, _ = validated_cache(tmp_path)
    (tmp_path/'imports.json').write_text(json.dumps(TORCH_VERSIONS))
    (tmp_path/'result-r3.json').write_text(json.dumps(dict(
        returncode=0, network='none', no_model_calls=True,
        task_source_mounted=False, official_tests_mounted=False)))
    return source, {TORCH_SEED_ENV: str(tmp_path/'cache-integrity.json')}


@pytest.mark.parametrize('task', ['torch-pipeline-parallelism', 'torch-tensor-parallelism'])
def test_torch_cache_is_private_and_only_applies_to_registered_task(tmp_path, task):
    source, env = validated_torch_cache(tmp_path)
    output = tmp_path/'attempt'
    assert seed_uv_cache('other-torch-task', output, env) is None
    assert not output.exists()
    result = seed_uv_cache(task, output, env)
    assert result['contract'].endswith('/v1') and result['independent_copy']
    (output/'cache/uv/package.whl').write_bytes(b'private change')
    assert (source/'package.whl').read_bytes() == b'verified public package'


@pytest.mark.parametrize('change', ['task_inputs', 'version', 'cache_content'])
def test_torch_cache_rejects_unvalidated_inputs_before_creating_attempt(tmp_path, change):
    source, env = validated_torch_cache(tmp_path)
    if change == 'task_inputs':
        p = tmp_path/'result-r3.json'
        data = json.loads(p.read_text()); data['official_tests_mounted'] = True
        p.write_text(json.dumps(data))
    elif change == 'version':
        p = tmp_path/'imports.json'
        data = json.loads(p.read_text()); data['torch'] = '2.7.1+cu126'
        p.write_text(json.dumps(data))
    else:
        (source/'package.whl').write_bytes(b'corrupt package')
    output = tmp_path/'attempt'
    with pytest.raises(ValueError):
        seed_uv_cache('torch-pipeline-parallelism', output, env)
    assert not output.exists()


def test_preinstall_defaults_off_and_is_scoped_to_hf(tmp_path):
    from experiments.scripts.tb_prerequisite_cache import install_fixed_prerequisites, PREINSTALL_ENV
    assert install_fixed_prerequisites('other',tmp_path,{PREINSTALL_ENV:'1'}) is None
    assert install_fixed_prerequisites('hf-train-lora-adapter',tmp_path,{}) is None


def test_preinstall_failure_stops_before_import_or_model(tmp_path,monkeypatch):
    import subprocess
    from experiments.scripts import tb_prerequisite_cache as cache
    (tmp_path/'prerequisite-cache.json').write_text(json.dumps(dict(cache_sha256='verified')))
    calls=[]
    def failed(command,**kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command,1,stdout='',stderr='offline cache miss')
    monkeypatch.setattr(cache.subprocess,'run',failed)
    with pytest.raises(subprocess.CalledProcessError):
        cache.install_fixed_prerequisites('hf-train-lora-adapter',tmp_path,{cache.PREINSTALL_ENV:'1'})
    assert len(calls)==1 and '--offline' in calls[0]
