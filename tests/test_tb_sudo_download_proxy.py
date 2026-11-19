from pathlib import Path

import pytest

from experiments.scripts import tb_sudo_download_proxy as proxy
from experiments.scripts.tb_download_proxy import environment
from experiments.scripts.run_tb_dual_local import result_configuration


def enable(monkeypatch):
    for key, value in environment().items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(proxy.os, 'geteuid', lambda: 0)


def test_only_registered_task_and_enabled_proxy_can_install(monkeypatch, tmp_path):
    directory = tmp_path/'sudoers.d'
    enable(monkeypatch)
    assert proxy.configure('other-task', directory) is None
    monkeypatch.delenv('AGENTTX_DOWNLOAD_PROXY_RETRY')
    assert proxy.configure(proxy.TASK, directory) is None
    assert not directory.exists()


def test_private_rule_preserves_routing_and_can_be_repeated(monkeypatch, tmp_path):
    enable(monkeypatch)
    first = proxy.configure(proxy.TASK, tmp_path)
    assert first == proxy.configure(proxy.TASK, tmp_path)
    target = Path(first['path'])
    assert target.stat().st_mode & 0o777 == 0o440
    assert target.read_text() == proxy.CONTENTS
    assert 'NO_PROXY' in target.read_text()
    assert 'PYOPENGL_PLATFORM' in target.read_text()
    assert 'ALL_PROXY' not in target.read_text()


def test_rejects_unvalidated_route_and_conflicting_rule(monkeypatch, tmp_path):
    enable(monkeypatch)
    monkeypatch.setenv('HTTPS_PROXY', 'http://wrong:8080')
    with pytest.raises(ValueError, match='validated'):
        proxy.configure(proxy.TASK, tmp_path)
    enable(monkeypatch)
    target = tmp_path/'agenttx-download-proxy'
    target.write_text('unrelated\n')
    with pytest.raises(ValueError, match='unrelated'):
        proxy.configure(proxy.TASK, tmp_path)
    assert target.read_text() == 'unrelated\n'


def test_repair_changes_pairing_configuration(tmp_path):
    path = tmp_path/'clean_recovery'/'host-result.json'
    before = result_configuration({}, path)
    after = result_configuration({'sudo_download_proxy_contract': proxy.CONTRACT}, path)
    assert before != after
    assert after['sudo_download_proxy_contract'] == proxy.CONTRACT


@pytest.mark.parametrize('error', [
    'ImportError: Unable to load OpenGL or GL library',
    "OpenGL/platform/glx.py AttributeError: glXGetCurrentContext",
])
def test_wrong_backend_requires_working_osmesa(error):
    assert proxy.stripped_environment_failure(error) is None
    passed = 'PASSED ../../tests/test_outputs.py::test_osmesa_is_runnable\n'
    assert proxy.stripped_environment_failure(passed + error) == 'explicit-osmesa-input-stripped-before-glx-import'
    assert proxy.stripped_environment_failure(passed + 'wrong rendered image') is None


def test_network_failure_requires_actual_duck_download():
    assert proxy.stripped_environment_failure('ConnectionResetError') is None
    assert proxy.stripped_environment_failure('Downloading Duck model ConnectionResetError') == 'github-download-without-sudo-proxy'
