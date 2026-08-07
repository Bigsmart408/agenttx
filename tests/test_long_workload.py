"""Structural tests for the deterministic long Coding Agent workload."""
from pathlib import Path

import pytest

from experiments.workloads.long_coding_traj import (
    DEFAULT_STEPS,
    MIN_STEPS,
    REPAIR_TAG,
    build_long_coding_trajectory,
    fault_step_index,
    find_tagged_step,
)


def test_long_workload_has_fixed_fault_and_repair() -> None:
    steps = build_long_coding_trajectory()
    assert len(steps) == DEFAULT_STEPS
    fault = fault_step_index(steps)
    repair = find_tagged_step(steps, REPAIR_TAG)
    assert fault < repair
    assert steps[fault].args["path"] == "lib/formatting.py"
    assert "build/format-report.txt" in steps[fault + 3].args.get("cmd", "")
    assert steps[-1].tool == "run_tests"
    assert steps[-1].args["tag"] == "final_check"


def test_long_workload_length_is_exact_and_paths_are_workspace_relative() -> None:
    steps = build_long_coding_trajectory(MIN_STEPS)
    assert len(steps) == MIN_STEPS
    allowed = {"write_file", "append_file", "read_file", "run_shell", "run_tests", "delete_file"}
    for step in steps:
        assert step.tool in allowed
        path = step.args.get("path")
        if path is not None:
            candidate = Path(str(path))
            assert not candidate.is_absolute()
            assert ".." not in candidate.parts
    with pytest.raises(ValueError):
        build_long_coding_trajectory(MIN_STEPS - 1)