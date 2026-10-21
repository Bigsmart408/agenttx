"""Prevent local Docker stages from becoming registry downloads."""
import pytest

from experiments.workloads.terminal_bench_suite import _domestic_dockerfile


def test_dynamic_stage_selection_and_platform_are_preserved(monkeypatch):
    monkeypatch.setenv('AGENTTX_TB_HUB_MIRROR', 'docker.1ms.run')
    original = ('ARG USER_PRIVILEGE_LEVEL=standard\n'
                'ARG UBUNTU_VERSION=22.04\n'
                'FROM --platform=linux/amd64 ubuntu:${UBUNTU_VERSION} AS stage-root\n'
                'FROM stage-root as stage-standard\n'
                'FROM stage-${USER_PRIVILEGE_LEVEL}\n')
    result = _domestic_dockerfile(original)
    assert 'FROM --platform=linux/amd64 docker.1ms.run/library/ubuntu:${UBUNTU_VERSION}' in result
    assert 'FROM stage-root as stage-standard\nFROM stage-${USER_PRIVILEGE_LEVEL}\n' in result


def test_copy_sources_distinguish_stages_contexts_and_external_images(monkeypatch):
    monkeypatch.setenv('AGENTTX_TB_GHCR_MIRRORS', 'ghcr.nju.edu.cn')
    original = ('FROM scratch AS builder\n'
                'COPY --from=builder /one /one\n'
                'COPY --from=0 /two /two\n'
                'COPY --from=agenttx-assets data /data\n'
                'COPY --from=ghcr.io/astral-sh/uv:0.7.13 /uv /bin/uv\n')
    result = _domestic_dockerfile(original)
    assert result == original.replace('ghcr.io/', 'ghcr.nju.edu.cn/')


def test_unregistered_external_registry_has_no_direct_fallback():
    with pytest.raises(ValueError, match='No registered domestic mirror'):
        _domestic_dockerfile('FROM quay.io/example/image:1\n')


def test_build_package_transport_keeps_explicit_cpu_index_and_pins():
    from experiments.workloads.terminal_bench_suite import _domestic_package_sources
    original=('FROM debian:bullseye-slim\n'
              'RUN pip install torch==2.7.0 --index-url https://download.pytorch.org/whl/cpu\n')
    text=_domestic_package_sources(original)
    assert original.splitlines()[1] in text
    assert 'UV_DEFAULT_INDEX=' not in text
    assert 'https://mirrors.tuna.tsinghua.edu.cn/debian-security' in text
    assert 'https://mirrors.aliyun.com/pypi/simple' in text


def test_lc0_offline_cpu_wheels_must_match_original_upstream_hashes(tmp_path):
    from experiments.workloads import terminal_bench_suite as tb
    source=tmp_path/'leelachess0-pytorch-conversion';source.mkdir()
    cache=tmp_path/'cache';cache.mkdir()
    for name in tb._LC0_CPU_WHEELS:(cache/name).write_bytes(b'wrong wheel bytes')
    script='RUN pip install torch==2.8.0 torchvision==0.23.0 --index-url https://download.pytorch.org/whl/cpu'
    with pytest.raises(RuntimeError,match='checksum mismatch'):
        tb._cached_build_bootstrap(source,script,cache)


def test_offline_build_installer_keeps_upstream_package_commands(tmp_path):
    from experiments.scripts.validate_tb_bootstrap_batch import effective_script
    original=('curl -LsSf https://astral.sh/uv/0.7.13/install.sh | sh\n'
              'source $HOME/.local/bin/env\nuv init\nuv add pytest==8.4.1\n'
              'uv pip install pillow==11.2.1 numpy==2.3.1\n')
    text,versions=effective_script(original)
    assert versions==[dict(upstream='0.7.13',resolved='0.7.13')]
    assert 'astral.sh' not in text
    assert text.endswith(original.split('\n',1)[1])


def test_qemu_security_archive_preserves_signed_index_and_alpine_release(tmp_path,monkeypatch):
    from experiments.workloads import terminal_bench_suite as tb
    monkeypatch.setenv('AGENTTX_DOMESTIC_ONLY','1')
    source=tmp_path/'qemu-startup';source.mkdir()
    (source/'Dockerfile').write_text('FROM debian:bullseye-slim\nRUN apt-get update && apt-get install -y qemu-utils\n'
        'RUN wget https://dl-cdn.alpinelinux.org/alpine/v3.19/releases/x86_64/alpine-virt-3.19.0-x86_64.iso\n')
    candidates=tb._dockerfile_candidates(source)
    try:
        text=candidates[0][0].read_text()
        assert 'http://archive.debian.org/debian-security' in text
        assert '[check-valid-until=no]' in text
        assert 'qemu-utils' in text and 'alpine-virt-3.19.0-x86_64.iso' in text
        assert 'trusted=yes' not in text and 'allow-unauthenticated' not in text
    finally:
        for path,_,_ in candidates:
            if path:path.unlink()


def test_sidecar_base_pull_finishes_before_build_and_keeps_local_stages(tmp_path,monkeypatch):
    import subprocess
    from experiments.scripts import validate_tb_sidecars_local as sidecars
    monkeypatch.setenv('AGENTTX_TB_HUB_MIRROR','docker.1ms.run')
    source=tmp_path/'source';source.mkdir();folder=tmp_path/'output';folder.mkdir()
    (source/'Dockerfile').write_text('FROM ubuntu:22.04 AS base\nFROM base\n')
    events=[]
    def pull(reference,destination):
        events.append(('pull',reference));return 'sha256:pinned-base'
    def run(command,**kwargs):
        events.append(('build',command));return subprocess.CompletedProcess(command,0)
    monkeypatch.setattr(sidecars,'pull',pull)
    monkeypatch.setattr(sidecars.subprocess,'run',run)
    monkeypatch.setattr(sidecars.subprocess,'check_output',lambda *a,**k:'[{"Id":"sha256:built"}]')
    assert sidecars.prepare_image(dict(build='.'),source,folder,'spring')=='sha256:built'
    assert events[0]==('pull','library/ubuntu:22.04')
    assert events[1][0]=='build'
    text=(folder/'spring.Dockerfile').read_text()
    assert 'FROM sha256:pinned-base AS base' in text
    assert 'FROM base\n' in text
