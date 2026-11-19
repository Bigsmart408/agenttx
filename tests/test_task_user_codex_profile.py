import os
import pwd
import stat
from pathlib import Path

import pytest

from experiments.scripts.experiment_credentials import task_user_codex_profile


def test_private_profile_is_identical_scoped_and_removed_on_error(tmp_path, monkeypatch):
    sources = {}
    for key, name, body in [('CODEX_CONFIG_FILE', 'config.toml', b'model_provider="existing"\n'),
                            ('CODEX_AUTH_FILE', 'auth.json', b'{"test":"private"}')]:
        source = tmp_path / name
        source.write_bytes(body)
        source.chmod(0o600)
        sources[key] = source
        monkeypatch.setenv(key, str(source))
    account = pwd.getpwuid(os.geteuid())
    copies = []
    with pytest.raises(ValueError, match='probe failure'):
        with task_user_codex_profile(account.pw_name, parent=tmp_path) as proof:
            assert proof['provider_configuration_changed'] is False
            assert proof['host_profile_modified'] is False
            for key, source in sources.items():
                copy = Path(os.environ[key])
                copies.append(copy)
                assert copy != source and copy.read_bytes() == source.read_bytes()
                assert stat.S_IMODE(copy.stat().st_mode) == 0o600
                assert stat.S_IMODE(copy.parent.stat().st_mode) == 0o700
            raise ValueError('probe failure')
    assert all(not p.exists() for p in copies)
    assert all(os.environ[k] == str(p) and stat.S_IMODE(p.stat().st_mode) == 0o600
               for k, p in sources.items())


def test_missing_profile_fails_before_any_default_provider_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv('CODEX_CONFIG_FILE', raising=False)
    monkeypatch.delenv('CODEX_AUTH_FILE', raising=False)
    with pytest.raises(RuntimeError, match='existing explicit Codex profile'):
        with task_user_codex_profile(pwd.getpwuid(os.geteuid()).pw_name, parent=tmp_path):
            pytest.fail('must not silently use defaults')
    assert not list(tmp_path.iterdir())
