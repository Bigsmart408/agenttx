import sys
from unittest.mock import Mock

import pytest

from experiments.scripts import verify_swe_workspace as verifier
from experiments.workloads import swe_bench_suite as swe


def test_probe_uses_pinned_image_without_network_or_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://invalid-proxy")
    monkeypatch.setattr(swe, "ensure_eval_image", lambda *_: pytest.fail("must not pull a pinned image"))
    output = Mock(side_effect=['sha256:pinned\n', '{"python_version":"3.6.13"}\n'])
    monkeypatch.setattr(swe.subprocess, "check_output", output)
    swe.official_runtime_info.cache_clear()
    try:
        runtime = swe.official_runtime_info("django__django-11019", "sha256:pinned")
        assert runtime["image_id"] == "sha256:pinned"
        assert runtime["python_version"] == "3.6.13"
        command = output.call_args_list[1].args[0]
        assert "--pull=never" in command and "--network=none" in command
        assert "HTTPS_PROXY" not in output.call_args_list[1].kwargs["env"]
    finally:
        swe.official_runtime_info.cache_clear()


def test_probe_rejects_changed_image_before_launching_container(monkeypatch):
    output = Mock(return_value="sha256:changed\n")
    monkeypatch.setattr(swe.subprocess, "check_output", output)
    swe.official_runtime_info.cache_clear()
    try:
        with pytest.raises(RuntimeError, match="identity mismatch"):
            swe.official_runtime_info("django__django-11019", "sha256:pinned")
        assert output.call_count == 1
    finally:
        swe.official_runtime_info.cache_clear()


@pytest.mark.parametrize("tests_rc,tests_ok,expected", [(0, True, 0), (1, False, 1), ("error", False, 70), ("timeout", False, 70)])
def test_verifier_keeps_task_failure_distinct_from_infrastructure(monkeypatch, tmp_path, tests_rc, tests_ok, expected):
    monkeypatch.setattr(sys, "argv", ["verify", "--instance-id", "case", "--workdir", str(tmp_path), "--image-id", "sha256:pinned", "--expected-python", "3.6.13"])
    monkeypatch.setattr(swe, "load_tasks", lambda _: {"case": object()})
    monkeypatch.setattr(swe, "fetch_instance", lambda *_: {})
    monkeypatch.setattr(swe, "official_runtime_info", lambda *_: {"image_id": "sha256:pinned", "python_version": "3.6.13"})
    def verify(*args, **kwargs):
        assert args[2]["_agenttx_eval_runtime"]["image_id"] == "sha256:pinned"
        return {"tests_rc": tests_rc, "tests_ok": tests_ok}
    monkeypatch.setattr(swe, "verify", verify)
    monkeypatch.setenv("AGENTTX_SWE_VERIFY", "host")
    assert verifier.main() == expected
    assert verifier.os.environ["AGENTTX_SWE_VERIFY"] == "docker"


def test_verifier_probe_exception_is_infrastructure_failure(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["verify", "--instance-id", "case"])
    monkeypatch.setattr(swe, "load_tasks", lambda _: {"case": object()})
    monkeypatch.setattr(swe, "fetch_instance", lambda *_: {})
    monkeypatch.setattr(swe, "official_runtime_info", Mock(side_effect=RuntimeError("image missing")))
    assert verifier.main() == 70


@pytest.mark.parametrize("image,python", [("sha256:changed", "3.6.13"), ("sha256:pinned", "3.11.17")])
def test_verifier_refuses_runtime_drift_before_running_tests(monkeypatch, image, python):
    monkeypatch.setattr(sys, "argv", ["verify", "--instance-id", "case", "--image-id", "sha256:pinned", "--expected-python", "3.6.13"])
    monkeypatch.setattr(swe, "load_tasks", lambda _: {"case": object()})
    monkeypatch.setattr(swe, "fetch_instance", lambda *_: {})
    monkeypatch.setattr(swe, "official_runtime_info", lambda *_: {"image_id": image, "python_version": python})
    monkeypatch.setattr(swe, "verify", Mock(side_effect=AssertionError("must not grade changed runtime")))
    assert verifier.main() == 70


def test_agent_prompt_exposes_actual_evaluator_interpreter(monkeypatch):
    monkeypatch.setenv("AGENTTX_RECOVERY_CONTEXT", "compact")
    runtime = {"image_id": "sha256:pinned", "python_version": "3.6.13"}
    instance = {"repo": "django/django", "base_commit": "base", "problem_statement": "fix media", "_agenttx_eval_runtime": runtime}
    command = swe.agent_test_command(swe.SHORT, instance, sys.executable)
    assert "verify_swe_workspace.py" in command and "--image-id sha256:pinned" in command
    assert "--expected-python 3.6.13" in command
    assert "Python 3.6.13" in swe.task_prompt(swe.SHORT, instance, sys.executable)
