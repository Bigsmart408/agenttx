import argparse
import json
from datetime import timedelta
from pathlib import Path

from experiments.scripts import run_system_microbenchmark as runner
from scripts.maintenance import monitor_system_microbenchmark_20261030 as monitor


def test_low_docker_disk_stops_dispatch_when_results_disk_has_space(tmp_path, monkeypatch):
    registration = dict(workers=8, profile_sha256={})
    cells = dict(runtime_gate=[], model_canary=[], main=[dict(state="pending")])
    (tmp_path / "registration.json").write_text(json.dumps(registration))
    (tmp_path / "cells.json").write_text(json.dumps(cells))
    monkeypatch.setattr(runner.subprocess, "check_output", lambda *a, **k: "/docker-data")
    monkeypatch.setattr(runner, "storage_free_gib", lambda p: 4000 if Path(p) == tmp_path else 80)
    calls = []
    monkeypatch.setattr(runner, "execute", lambda *a: calls.append(a))
    runner.run_campaign(argparse.Namespace(cohort=tmp_path))
    progress = json.loads((tmp_path / "progress.json").read_text())
    assert calls == []
    assert progress["phase"] == "stopped"
    assert progress["stop_reason"] == "free_disk_below_100_GiB:docker"
    assert progress["storage_free_gib"] == dict(results=4000, docker=80)


def test_monitor_alerts_for_docker_disk_independently(tmp_path, monkeypatch):
    now = monitor.datetime.now(monitor.timezone.utc).isoformat()
    (tmp_path / "progress.json").write_text(json.dumps(dict(
        phase="main", started_at=now, updated_at=now, workers=8)))
    old = (monitor.datetime.now(monitor.timezone.utc) - timedelta(hours=2)).isoformat()
    (tmp_path / "cells.json").write_text(json.dumps(dict(
        runtime_gate=[dict(state="passed", finished_at=old)], model_canary=[], main=[])))

    def command(argv):
        if argv[0] == "systemctl":
            return "ActiveState=active\nSubState=running\nMainPID=123\n"
        if "info" in argv:
            return "/docker-data\n"
        return ""

    monkeypatch.setattr(monitor, "command", command)
    monkeypatch.setattr(monitor, "storage_free_gib", lambda p: 4000 if Path(p) == tmp_path else 80)
    result = monitor.sample(argparse.Namespace(cohort=tmp_path, service="test.service"), {})
    assert "docker_disk_free_below_100GiB" in result["alerts"]
    assert "docker_disk_free_below_150GiB" in result["alerts"]
    assert not any(s.startswith("results_disk_free") for s in result["alerts"])
    assert "no_terminal_result_for_30min" not in result["alerts"]
