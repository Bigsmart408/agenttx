"""Check verifier-only pinning, failure propagation, and comparison isolation."""
import json
import os
import subprocess

import pytest

from experiments.scripts import tb_verifier_cargo_lock as lock
from experiments.scripts import run_tb_dual_local as runner


def invoke(tmp_path, arguments, returncode=0):
    binary = tmp_path / 'cargo'
    binary.write_text('#!/bin/bash\nprintf "%s\\n" "$*" >> "$CALLS"\nexit "$CARGO_RC"\n')
    binary.chmod(0o755)
    env = dict(os.environ, PATH=str(tmp_path) + ':' + os.environ['PATH'],
               CALLS=str(tmp_path / 'calls'), CARGO_RC=str(returncode))
    code = lock.SHELL + '\nbash -ec ' + repr('cargo ' + arguments)
    result = subprocess.run(['bash', '-ec', code], env=env,
                            capture_output=True, text=True, timeout=10)
    calls = (tmp_path / 'calls').read_text().splitlines()
    events = [json.loads(line) for line in result.stderr.splitlines()
              if line.startswith('{"contract":')]
    return result, calls, events


def test_original_geiger_version_uses_published_lock_once(tmp_path):
    result, calls, events = invoke(tmp_path, 'install cargo-geiger --version 0.12.0')
    assert result.returncode == 0
    assert calls == ['install cargo-geiger --version 0.12.0 --locked']
    assert len(events) == 1 and events[0]['contract'] == lock.CONTRACT


@pytest.mark.parametrize('arguments', [
    'test --quiet', 'geiger --output-format Json',
    'install cargo-geiger --version 0.12.0 --locked',
    'install cargo-geiger --version 0.13.0', 'install other-tool --version 0.12.0',
])
def test_other_commands_and_explicit_lock_are_unchanged(tmp_path, arguments):
    result, calls, events = invoke(tmp_path, arguments, returncode=7)
    assert result.returncode == 7
    assert calls == [arguments] and not events


def test_install_error_propagates_without_retry_or_success_fabrication(tmp_path):
    result, calls, events = invoke(tmp_path, 'install cargo-geiger --version 0.12.0', returncode=101)
    assert result.returncode == 101 and len(calls) == 1 and len(events) == 1


def test_legacy_and_locked_verifier_histories_are_not_paired(tmp_path):
    path = tmp_path / 'host-result.json'
    old = runner.result_configuration({}, path)
    new = runner.result_configuration({'verifier_toolchain_contract': lock.CONTRACT}, path)
    assert runner.configuration_id(old) != runner.configuration_id(new)
