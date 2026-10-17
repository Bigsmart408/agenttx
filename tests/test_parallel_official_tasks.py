import signal
from pathlib import Path

import pytest

from agenttx.semisolate import _probe_try_backend
from experiments.scripts.run_parallel_official_tasks import (
    DEFAULT_MODES,
    catalog_tasks,
    merged_rows,
    parse_args,
    partition_tasks,
    partition_work,
    result_shard_count,
    recovery_context_by_mode,
    result_subdir,
    signal_stop_reason,
    stop_reason_for_status,
    worker_command,
    worker_environment,
)


def test_catalog_tasks_filters_suite_before_launch(monkeypatch):
    from experiments.workloads import swe_bench_suite as swe
    from experiments.workloads import terminal_bench_suite as tb

    monkeypatch.setattr(swe, "load_tasks", lambda _cache: {"swe-case": object()})
    monkeypatch.setattr(tb, "load_tasks", lambda _cache: {"tb-case": object()})

    assert catalog_tasks(Path("cache"), "swe") == ["swe:swe-case"]
    assert catalog_tasks(Path("cache"), "tb") == ["tb:tb-case"]


def test_default_modes_cover_the_complete_official_experiment_matrix():
    assert DEFAULT_MODES == (
        "no_fault",
        "crash_direct",
        "clean_recovery",
        "crash_no_rollback",
        "causal",
        "temporal_checkpoint",
        "whole_branch_abort",
    )


def test_partition_tasks_is_complete_balanced_and_disjoint():
    tasks = [f"task-{index}" for index in range(17)]

    shards = partition_tasks(tasks, 4)

    assert sorted(task for shard in shards for task in shard) == sorted(tasks)
    assert max(map(len, shards)) - min(map(len, shards)) <= 1
    assert sum(map(len, shards)) == len(set(task for shard in shards for task in shard))


def test_mode_parallel_assigns_one_full_sweep_per_mode():
    tasks = ["task-a", "task-b"]
    modes = ["crash_no_rollback", "causal", "temporal_checkpoint", "whole_branch_abort"]

    assignments = partition_work(tasks, modes, 4, mode_parallel=True)

    assert assignments == [(tasks, [mode]) for mode in modes]


def test_mode_parallel_creates_one_queued_sweep_per_mode():
    tasks = ["task-a", "task-b"]

    assignments = partition_work(tasks, DEFAULT_MODES, 4, mode_parallel=True)

    assert len(assignments) == len(DEFAULT_MODES)
    assert all(worker_tasks == tasks for worker_tasks, _ in assignments)
    assert [modes for _, modes in assignments] == [[mode] for mode in DEFAULT_MODES]


def test_mode_parallel_can_prioritize_a_paired_prefix():
    tasks = ["task-a", "task-b", "task-c"]
    modes = ["clean_recovery", "causal", "whole_branch_abort"]

    assignments = partition_work(
        tasks, modes, 2, mode_parallel=True, paired_prefix=1
    )

    assert assignments == [
        (["task-a"], ["clean_recovery"]),
        (["task-a"], ["causal"]),
        (["task-a"], ["whole_branch_abort"]),
        (["task-b", "task-c"], ["clean_recovery"]),
        (["task-b", "task-c"], ["causal"]),
        (["task-b", "task-c"], ["whole_branch_abort"]),
    ]


def test_mode_parallel_cli_accepts_fewer_workers_than_modes():
    args = parse_args(["--mode-parallel", "--workers", "4"])

    assert args.mode_parallel is True
    assert args.workers == 4


def test_mode_parallel_cli_can_pause_one_mode():
    args = parse_args([
        "--mode-parallel", "--workers", "4", "--modes", "causal",
        "clean_recovery", "temporal_checkpoint", "whole_branch_abort",
        "--skip-modes", "causal", "--resume-scale-workers",
    ])

    assert args.skip_modes == ["causal"]
    assert args.resume_scale_workers is True


def test_paired_prefix_requires_mode_parallel():
    try:
        parse_args(["--paired-prefix", "1"])
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("parse_args should reject paired prefix without mode parallel")


def test_randomized_crash_is_an_explicit_runner_option():
    args = parse_args(["--randomize-crash"])

    assert args.randomize_crash is True


def test_result_subdir_is_stable_per_shard():
    assert result_subdir("screen", 3) == "screen/shards/shard_03"


def test_result_shard_count_uses_frozen_queue_width(monkeypatch, tmp_path):
    from experiments.scripts import run_parallel_official_tasks as parallel

    monkeypatch.setattr(parallel, "ROOT", tmp_path)
    result_dir = tmp_path / "experiments" / "results" / "run"
    result_dir.mkdir(parents=True)
    (result_dir / "run_config.json").write_text(
        '{"workers":4,"result_shards":7}', encoding="utf-8"
    )

    assert result_shard_count("run", 4) == 7


def test_mode_parallel_resume_can_scale_workers_without_changing_workload(monkeypatch, tmp_path):
    import json

    from experiments.scripts import run_parallel_official_tasks as parallel

    monkeypatch.setattr(parallel, "ROOT", tmp_path)
    monkeypatch.setattr(parallel.subprocess, "run", lambda *args, **kwargs: type(
        "Result", (), {"stdout": "commit\n"}
    )())
    modes = ["causal", "clean_recovery", "temporal_checkpoint"]
    tasks = ["swe:one"]
    base = [
        "--run-name", "run", "--mode-parallel", "--workers", "2",
        "--max-requests", "2", "--modes", *modes,
        "--model", "gpt-5.6-luna", "--reasoning-effort", "max",
    ]
    env = {"AGENTTX_KEEP_EVAL_IMAGES": "1", "AGENTTX_FAULT_SEED": "7"}
    original = parallel.write_run_config(parallel.parse_args(base), tasks, env)
    scaled_args = parallel.parse_args(base + ["--resume-scale-workers"])
    scaled_args.workers = 3
    scaled_args.max_requests = 3
    assert parallel.write_run_config(scaled_args, tasks, env) == original
    config = json.loads(original.read_text())
    assert config["workers"] == 3
    assert config["max_concurrent_requests"] == 3
    assert config["result_shards"] == 3


def test_parallel_runner_defaults_to_four_external_requests():
    args = parse_args([])

    assert args.workers == 8
    assert args.max_requests == 4
    assert args.reasoning_effort == "max"
    assert args.max_turns is None
    assert args.suite == "all"


def test_context_matrix_matches_compact_environment_for_all_policies():
    modes = ["causal", "temporal_checkpoint", "whole_branch_abort", "crash_no_rollback"]
    assert recovery_context_by_mode(modes, {"AGENTTX_RECOVERY_CONTEXT": "compact"}, "inherit", "inherit") == dict.fromkeys(modes, "compact")


def test_resuming_after_source_change_is_rejected_even_when_worker_scaling_is_allowed(monkeypatch, tmp_path):
    from experiments.scripts import run_parallel_official_tasks as parallel

    monkeypatch.setattr(parallel, "ROOT", tmp_path)
    monkeypatch.setattr(parallel.subprocess, "run", lambda *args, **kwargs: type("Result", (), {"stdout": "commit\n"})())
    source = tmp_path / "src/agenttx/runtime.py"
    source.parent.mkdir(parents=True)
    source.write_text("before\n")
    args = parallel.parse_args(["--run-name", "run", "--mode-parallel", "--workers", "1", "--resume-scale-workers", "--modes", "causal"])
    config = parallel.write_run_config(args, ["swe:one"], {"AGENTTX_KEEP_EVAL_IMAGES": "1"})
    original = config.read_bytes()
    source.write_text("after\n")
    with pytest.raises(RuntimeError, match="configuration changed"):
        parallel.write_run_config(args, ["swe:one"], {"AGENTTX_KEEP_EVAL_IMAGES": "1"})
    assert config.read_bytes() == original


def test_clean_recovery_context_override_is_worker_local():
    base = {"AGENTTX_RECOVERY_CONTEXT": "full"}

    clean = worker_environment(base, ["clean_recovery"], "compact", "inherit")
    causal = worker_environment(base, ["causal"], "compact", "inherit")

    assert clean["AGENTTX_RECOVERY_CONTEXT"] == "compact"
    assert causal["AGENTTX_RECOVERY_CONTEXT"] == "full"
    assert base["AGENTTX_RECOVERY_CONTEXT"] == "full"


def test_runner_workers_do_not_inherit_proxy_environment():
    from experiments.scripts import run_parallel_official_tasks as parallel

    base = {
        "AGENTTX_RECOVERY_CONTEXT": "full",
        "HTTP_PROXY": "http://127.0.0.1:17897",
        "https_proxy": "http://127.0.0.1:17897",
    }
    direct = parallel.worker_environment(base, ["causal"], "inherit", "compact")

    assert "HTTP_PROXY" not in direct
    assert "https_proxy" not in direct
    assert direct["NO_PROXY"] == "*"
    assert direct["AGENTTX_NO_PROXY"] == "1"


def test_causal_recovery_context_override_is_worker_local():
    base = {"AGENTTX_RECOVERY_CONTEXT": "full"}

    causal = worker_environment(base, ["causal"], "inherit", "compact")
    temporal = worker_environment(base, ["temporal_checkpoint"], "inherit", "compact")

    assert causal["AGENTTX_RECOVERY_CONTEXT"] == "compact"
    assert temporal["AGENTTX_RECOVERY_CONTEXT"] == "full"
    assert base["AGENTTX_RECOVERY_CONTEXT"] == "full"


def test_recovery_context_matrix_records_clean_compact_and_other_modes_full():
    contexts = recovery_context_by_mode(
        DEFAULT_MODES,
        {"AGENTTX_RECOVERY_CONTEXT": "full"},
        "compact",
        "compact",
    )

    assert contexts == {
        "no_fault": "none",
        "crash_direct": "none",
        "clean_recovery": "compact",
        "crash_no_rollback": "full",
        "causal": "compact",
        "temporal_checkpoint": "full",
        "whole_branch_abort": "full",
    }


def test_clean_recovery_context_cli_is_explicit():
    args = parse_args(
        ["--clean-recovery-context", "compact", "--causal-recovery-context", "compact"]
    )

    assert args.clean_recovery_context == "compact"
    assert args.causal_recovery_context == "compact"


def test_worker_command_pins_reasoning_effort(monkeypatch):
    monkeypatch.setattr(
        "experiments.scripts.run_parallel_official_tasks.shutil.which",
        lambda name: "/usr/bin/codex" if name == "codex" else None,
    )

    command = worker_command(
        tasks=["tb:hello-world"],
        run_name="run",
        shard_index=0,
        modes=["no_fault"],
        repeats=1,
        model="gpt-5.6-luna",
        reasoning_effort="max",
        harness_timeout=1800,
        python="/opt/conda/bin/python",
    )

    harness = command[command.index("--harness-command") + 1]
    assert "gpt-5.6-luna" in harness
    assert 'model_reasoning_effort=\"max\"' in harness
    assert "--allow-result-failures" in command


def test_sweagent_worker_uses_native_harness_and_luna_max(monkeypatch):
    monkeypatch.setattr(
        "experiments.scripts.run_parallel_official_tasks.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )

    command = worker_command(
        tasks=["swe:case"],
        run_name="sweagent-run",
        shard_index=1,
        modes=["causal"],
        repeats=1,
        model="gpt-5.6-luna",
        reasoning_effort="max",
        harness_timeout=1800,
        python="/opt/conda/bin/python",
        harness="sweagent",
        suite="swe",
    )

    assert command[command.index("--suite") + 1] == "swe"
    assert command[command.index("--harness") + 1] == "sweagent"
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    assert command[command.index("--reasoning-effort") + 1] == "max"
    assert "--harness-command" not in command
    assert "--max-turns" not in command
    assert command[0] == "/usr/bin/bwrap"
    assert command[command.index("--setenv") + 2] == "/root"
    assert "AGENTTX_MOTIVATION_BWRAP" in command


def test_sweagent_step_cap_is_only_applied_when_requested(monkeypatch):
    monkeypatch.setattr(
        "experiments.scripts.run_parallel_official_tasks.shutil.which",
        lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
    )
    command = worker_command(
        tasks=["swe:case"], run_name="run", shard_index=0,
        modes=["causal"], repeats=1, model="gpt-5.6-luna",
        reasoning_effort="max", harness_timeout=1800,
        python="/opt/conda/bin/python", harness="sweagent", max_turns=192,
    )

    assert command[command.index("--max-turns") + 1] == "192"


def test_merged_rows_can_include_environment_failures(monkeypatch, tmp_path):
    from experiments.scripts import run_parallel_official_tasks as parallel
    from experiments.scripts import bench_official_tasks as bench

    monkeypatch.setattr(parallel, "ROOT", tmp_path)
    rows = [
        {
            "suite": "swe",
            "task": "ok",
            "mode": "causal",
            "repeat": 0,
            "harness_backend": "codex",
            "finished": True,
            "finish_called": True,
            "environment_error": False,
            "error": "",
        },
        {
            "suite": "swe",
            "task": "pull-failed",
            "mode": "causal",
            "repeat": 0,
            "harness_backend": "codex",
            "finished": False,
            "finish_called": False,
            "environment_error": True,
            "error": "docker pull failed",
        },
    ]
    monkeypatch.setattr(bench, "_load_prior_rows", lambda _path: rows)

    assert len(merged_rows("run", 1, ["causal"])) == 1
    assert len(merged_rows("run", 1, ["causal"], completed_only=False)) == 2


def test_merged_rows_follow_frozen_task_mode_and_repeat_order(monkeypatch, tmp_path):
    from experiments.scripts import run_parallel_official_tasks as parallel
    from experiments.scripts import bench_official_tasks as bench

    monkeypatch.setattr(parallel, "ROOT", tmp_path)
    result_dir = tmp_path / "experiments" / "results" / "run"
    result_dir.mkdir(parents=True)
    (result_dir / "run_config.json").write_text(
        '{"tasks":["swe:task-b","swe:task-a"],"modes":["causal"]}',
        encoding="utf-8",
    )
    rows = [
        {
            "suite": "swe",
            "task": task,
            "mode": "causal",
            "repeat": repeat,
            "harness_backend": "codex",
            "finished": True,
            "finish_called": True,
            "environment_error": False,
            "error": "",
        }
        for task, repeat in (("task-a", 1), ("task-b", 1), ("task-a", 0), ("task-b", 0))
    ]
    monkeypatch.setattr(bench, "_load_prior_rows", lambda _path: rows)

    ordered = merged_rows("run", 1, ["causal"])

    assert [(row["task"], row["repeat"]) for row in ordered] == [
        ("task-b", 0),
        ("task-b", 1),
        ("task-a", 0),
        ("task-a", 1),
    ]


def test_try_probe_can_reuse_parent_validation(monkeypatch, tmp_path):
    monkeypatch.setenv("AGENTTX_ASSUME_TRY_AVAILABLE", "1")

    assert _probe_try_backend(tmp_path / "missing-try", tmp_path) == (
        True,
        "validated by parent supervisor",
    )


def test_try_probe_timeout_is_configurable(monkeypatch, tmp_path):
    import subprocess

    import agenttx.semisolate as semisolate

    captured = {}

    def fake_run(*args, **kwargs):
        captured["timeout"] = kwargs["timeout"]
        return subprocess.CompletedProcess(args[0], 0, "", "")

    monkeypatch.delenv("AGENTTX_ASSUME_TRY_AVAILABLE", raising=False)
    monkeypatch.setenv("AGENTTX_TRY_PROBE_TIMEOUT_S", "12.5")
    monkeypatch.setattr(semisolate.subprocess, "run", fake_run)

    assert _probe_try_backend(tmp_path / "try-configurable", tmp_path)[0] is True
    assert captured["timeout"] == 12.5


def test_supervisor_does_not_abort_on_cell_environment_error():
    assert stop_reason_for_status([], {"environment_errors": 1}) == ""
    assert stop_reason_for_status([(2, 1)], {"environment_errors": 0}) == (
        "shard failure detected: [(2, 1)]"
    )


def test_supervisor_reports_external_signal():
    assert signal_stop_reason(signal.SIGTERM) == (
        "received SIGTERM; stopping all shards"
    )
