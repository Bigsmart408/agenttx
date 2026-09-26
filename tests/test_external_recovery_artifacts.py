from pathlib import Path

from experiments.scripts.bench_external_recovery_artifacts import (
    BackendOutcome,
    Timing,
    motivation_400_cases,
    parse_snapshot_id,
    present_ids,
    result_row,
    waypoint_case_script,
)
from experiments.workloads.intra_turn_recovery_dag import build_multi_turn_plan


def plan():
    return build_multi_turn_plan(
        2,
        8,
        fault_position=0.25,
        dependency_value=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )


def test_present_ids_reads_artifact_markers(tmp_path: Path) -> None:
    case = plan()
    for step in case.steps:
        (tmp_path / step.relative_path).write_text("BASE\n", encoding="utf-8")
    for index in (1, 4, 9):
        (tmp_path / case.steps[index].relative_path).write_text(
            f"artifact-{index:03d}\n", encoding="utf-8"
        )
    assert present_ids(tmp_path, case) == frozenset({1, 4, 9})


def test_result_row_derives_metrics_from_recovered_workspace() -> None:
    case = plan()
    invalid = set(case.oracle_closure)
    valid = set(case.valid_ids)
    outcome = BackendOutcome(
        present_ids=frozenset(valid),
        timing=Timing(1.0, 2.0, 3.0),
        native_boundary="test",
        artifact_commit="abc",
    )
    row = result_row("agenttx", case, outcome)
    assert row["invalid_removal"] == 1.0
    assert row["valid_retention"] == 1.0
    assert row["exact_selective_state"] is True
    assert row["removed_ids"]
    assert invalid


def test_result_row_marks_coarse_whole_abort_safe_but_wasteful() -> None:
    case = plan()
    row = result_row(
        "branchfs",
        case,
        BackendOutcome(
            present_ids=frozenset(),
            timing=Timing(1.0, 2.0, 3.0),
            native_boundary="leaf_branch",
            artifact_commit="abc",
        ),
    )
    assert row["recovery_safe"] is True
    assert row["invalid_removal"] == 1.0
    assert row["valid_retention"] == 0.0
    assert row["exact_selective_state"] is False


def test_parse_snapshot_id_accepts_yolo_output() -> None:
    assert parse_snapshot_id('snapshot 7 "before-turn-2"\n') == 7


def test_waypoint_case_uses_fault_turn_checkpoint() -> None:
    case = plan()
    script = waypoint_case_script(case)
    fault_turn = case.steps[case.fault_step_id].turn_index
    assert 'checkpoint "$SESSION" turn-0' in script
    assert 'checkpoint "$SESSION" turn-1' in script
    assert f'fork "$SESSION" turn-{fault_turn} --id recovered' in script
    assert "__AGENTTX_PRESENT__" in script


def test_motivation_400_suite_matches_registered_manifests() -> None:
    cases = motivation_400_cases()
    counts = {
        suite_slice: sum(case.suite_slice == suite_slice for case in cases)
        for suite_slice in {case.suite_slice for case in cases}
    }
    assert counts == {
        "mb2_task_length": 100,
        "mb3_fault_position": 90,
        "mb4_dependency": 210,
    }
    assert len({(case.suite_slice, case.plan.trace_id) for case in cases}) == 400
