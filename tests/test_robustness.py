from pathlib import Path

import pytest

from agenttx.runtime import AgentTX
from experiments.scripts.bench_robustness import percentile, run_concurrent_agents


def test_percentile_uses_interpolation() -> None:
    assert percentile([1, 2, 3, 4], 0.50) == 2.5
    assert percentile([1, 2, 3, 4], 0.95) == pytest.approx(3.85)


def test_worker_crash_falls_back_and_restarts(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    session = tmp_path / "session"
    ws.mkdir()
    tx = AgentTX.begin(workdir=ws, session_dir=session, trace_reads=False)
    try:
        first = tx.run_tool("first", ["bash", "-c", "echo first > first.txt"])
        assert first.returncode == 0
        assert tx.pool is not None and tx.pool._worker_process is not None
        old_worker = tx.pool._worker_process
        tx.pool.inject_worker_crash_once()
        recovered = tx.run_tool("recovered", ["bash", "-c", "echo recovered > recovered.txt"])
        assert recovered.returncode == 0
        assert tx.pool.worker_failure_count == 1
        restarted = tx.run_tool("restarted", ["bash", "-c", "echo restarted > restarted.txt"])
        assert restarted.returncode == 0
        assert tx.pool._worker_process is not None
        assert tx.pool._worker_process is not old_worker
        tx.commit()
        assert (ws / "recovered.txt").read_text(encoding="utf-8") == "recovered\n"
    finally:
        tx.close(destroy=True)


def test_concurrent_agents_are_isolated() -> None:
    result = run_concurrent_agents(agents=2, steps=2)
    assert result["ok"] is True
    assert result["cross_contamination"] is False
    assert result["successful_agents"] == 2


def test_bpf_persistent_restart_is_bounded(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    session = tmp_path / "session"
    ws.mkdir()
    tx = AgentTX.begin(workdir=ws, session_dir=session, trace_reads=False)
    try:
        assert tx.pool is not None

        def boom(_request):
            raise RuntimeError("worker down")

        tx.pool._dispatch_worker = boom  # type: ignore[method-assign]
        tx.pool._stop_persistent_bpf = lambda: None  # type: ignore[method-assign]
        tx.pool._stop_worker = lambda: None  # type: ignore[method-assign]
        tx.pool._repair_worker_sandbox = lambda: None  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="persistent bpf worker failed 3 times"):
            tx.pool._run_step_bpf_persistent(["true"], [])
        assert tx.pool.worker_failure_count == 3
    finally:
        tx.close(destroy=True)

