"""Exercise the Clean workload path without a model, container, or real timer."""

from types import SimpleNamespace

import pytest

from experiments.scripts import sweagent_native_workload as workload

_ADAPTER_RUN = workload.SWEAgentNativeAdapter.run


@pytest.mark.parametrize("duration,swallow_alarm,expected_rc,clean_baseline", [
    (9.0, False, 0, False),
    (11.0, False, 124, False),
    (11.0, True, 124, False),
    (1.0, False, 127, True),
])
def test_clean_checks_elapsed_deadline_after_step(
    monkeypatch, tmp_path, duration, swallow_alarm, expected_rc, clean_baseline,
):
    clock = [0.0]
    calls = []
    stats = SimpleNamespace(tokens_sent=8, tokens_received=2, api_calls=1)
    agent = SimpleNamespace(
        model=SimpleNamespace(stats=stats), trajectory=[],
        save_trajectory=lambda: None,
    )

    class Environment:
        def __init__(self, **kwargs):
            self.deployment = object()

        def start(self):
            pass

        def communicate(self, *args, **kwargs):
            pass

        def set_env_variables(self, *args):
            pass

        def close(self):
            pass

    class Adapter:
        def __init__(self, **kwargs):
            self.agent = kwargs["agent"]

        def setup(self, *args):
            calls.append("setup")

        def step(self):
            calls.append("step")
            if swallow_alarm:
                try:
                    workload._raise_wall_timeout(None, None)
                except workload._SWEAgentWallTimeout:
                    pass
            clock[0] += duration
            agent.trajectory.append({"action": "submit"})
            return SimpleNamespace(done=not clean_baseline)

        def finish(self, **kwargs):
            calls.append("finish")
            return SimpleNamespace(
                summary="submitted", prompt_tokens=8, completion_tokens=2,
                total_tokens=10, tool_calls=1, model_calls=1, usage_source="offline-test",
            )

        def run(self, environment, problem, output, **kwargs):
            # Use the real adapter loop for an accurate pre-fix comparison.
            return _ADAPTER_RUN(self, environment, problem, output, **kwargs)

    monkeypatch.setenv("AGENTTX_MOTIVATION_BWRAP", "1")
    monkeypatch.setattr(workload.time, "perf_counter", lambda: clock[0])
    monkeypatch.setattr(workload.signal, "signal", lambda *args: object())
    monkeypatch.setattr(workload.signal, "setitimer", lambda *args: None)
    monkeypatch.setattr(workload, "_cleanup_sweagent_runtime", lambda: None)
    monkeypatch.setattr(workload, "_initialize_workspace_repo", lambda *args, **kwargs: None)
    monkeypatch.setattr(workload, "get_agent_from_config", lambda *args: agent)
    monkeypatch.setattr(workload, "get_deployment", lambda *args: object())
    monkeypatch.setattr(workload, "SWEEnv", Environment)
    monkeypatch.setattr(workload, "SWEAgentNativeAdapter", Adapter)

    runner = workload.SWEAgentNativeWorkloadHarness.__new__(
        workload.SWEAgentNativeWorkloadHarness,
    )
    runner.output_dir = tmp_path / "output"
    runner.model = "offline-test"
    runner.config_hash = "offline-test"
    runner.profile = SimpleNamespace(public_dict=lambda: {})
    runner.timeout_s = 10.0
    runner.max_steps = None
    runner.config = SimpleNamespace(
        tools=SimpleNamespace(execution_timeout=10, total_execution_timeout=11),
        model_copy=lambda **kwargs: SimpleNamespace(templates=SimpleNamespace()),
    )
    transaction = SimpleNamespace(
        workdir=tmp_path,
        tx=SimpleNamespace(pool=SimpleNamespace(session_dir=tmp_path / "session")),
    )

    task = ("This is the clean compact recovery baseline. Submit."
            if clean_baseline else "Complete this clean task.")
    result = runner.run_in_transaction(transaction, task)

    assert result.returncode == expected_rc, result.stderr
    assert result.total_tokens == 10
    assert calls.count("step") == 1
    assert ("finish" in calls) is (expected_rc == 0)
    if expected_rc == 124:
        assert result.metadata["sweagent_exit_status"] == "_SWEAgentWallTimeout"
