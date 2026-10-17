from types import SimpleNamespace

import pytest

from experiments.scripts import runtime_preflight as preflight


@pytest.mark.parametrize("strace,returncode,expected", [(True, 0, True), (False, 0, False), (True, 1, False)])
def test_required_checks_control_readiness_even_without_optional_bpf(tmp_path, monkeypatch, strace, returncode, expected):
    wrapper = tmp_path / "scripts/try-wrapper.sh"
    wrapper.parent.mkdir()
    wrapper.write_text("#!/bin/sh\n")
    wrapper.chmod(0o755)
    binary = tmp_path / "third_party/try/try"
    binary.parent.mkdir(parents=True)
    binary.touch()
    monkeypatch.setattr(preflight.shutil, "which", lambda name: "/usr/bin/strace" if name == "strace" and strace else None)
    monkeypatch.setattr(preflight.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=returncode, stdout="", stderr=""))
    report = preflight.runtime_preflight(tmp_path)
    assert report["ok"] is expected
    assert next(c for c in report["checks"] if c["name"] == "bpftrace")["required"] is False


def test_missing_try_blocks_before_attempting_execution(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight.shutil, "which", lambda _: "/usr/bin/tool")
    monkeypatch.setattr(preflight.subprocess, "run", lambda *args, **kwargs: pytest.fail("missing backend must not execute"))
    assert preflight.runtime_preflight(tmp_path)["ok"] is False
