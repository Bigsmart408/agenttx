from experiments.scripts.analyze_intra_turn_traces import closure, components
from experiments.scripts.bench_intra_turn_replay import (
    audit_replay_write_events,
    bootstrap_mean_ci,
    desired,
    injected_stale_payload,
    localize_artifact_faults,
    make_manifest,
    prepare,
    prepare_recovery,
    recovery_targets_from_localization,
    rollback_targets,
)
from experiments.workloads.intra_turn_recovery_dag import (
    INTERLEAVINGS,
    SHAPES,
    build_extreme_plan,
    build_intra_turn_plan,
    causal_closure,
)


def test_extreme_plan_has_required_sparse_closure():
    plan = build_extreme_plan()
    assert len(plan.steps) == 128
    assert plan.fault_step_id == 1
    assert plan.oracle_closure == (1, 32, 64, 127)
    assert len(plan.valid_ids) == 124
    assert causal_closure(plan.steps, [1]) == plan.oracle_closure


def test_parameter_grid_realizes_requested_closure():
    for width in (8, 16, 32, 64, 128):
        for fault in (0.05, 0.25, 0.5, 0.75):
            for ratio in (0.03, 0.10, 0.25, 0.50):
                for shape in SHAPES:
                    for interleaving in INTERLEAVINGS:
                        plan = build_intra_turn_plan(
                            width,
                            fault_position=fault,
                            closure_ratio=ratio,
                            shape=shape,
                            interleaving=interleaving,
                        )
                        expected = max(1, round(width * ratio))
                        assert len(plan.oracle_closure) == expected
                        assert plan.fault_step_id == min(plan.oracle_closure)


def test_audit_graph_uses_directed_closure_and_weak_components():
    nodes = {0, 1, 2, 3, 4}
    parents = {0: set(), 1: {0}, 2: {1}, 3: set(), 4: {3}}
    assert closure(nodes, parents, 0) == {0, 1, 2}
    assert {frozenset(group) for group in components(nodes, parents)} == {
        frozenset({0, 1, 2}),
        frozenset({3, 4}),
    }


def test_replay_manifest_does_not_disclose_recovery_policy():
    plan = build_extreme_plan()
    manifest = make_manifest(plan, [1, 32, 64, 127])
    assert "policy" not in manifest
    assert manifest["fault_step_id"] is None
    assert manifest["missing"] == []
    assert manifest["repair"] == []
    assert "retained_count" not in manifest
    assert manifest["artifact_contract"]["count"] == 128
    assert manifest["artifact_contract"]["index_start"] == 0
    assert manifest["artifact_contract"]["index_end_exclusive"] == 128
    assert "tool invocations may batch" in manifest["artifact_contract"]["replay_unit"]


def test_replay_incremental_bootstrap_accepts_no_complete_pairs():
    assert bootstrap_mean_ci([]) == (0.0, 0.0)


def test_replay_write_audit_covers_separate_completed_write_events():
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "read",
                "type": "command_execution",
                "command": "cat step_001.txt step_002.txt",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "write-1",
                "type": "command_execution",
                "command": "printf valid > step_001.txt",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "write-2",
                "type": "command_execution",
                "command": "printf valid > step_002.txt",
            },
        },
    ]

    audit = audit_replay_write_events(events, {"step_001.txt", "step_002.txt"})

    assert audit["followed"] is True
    assert audit["write_events"] == 2
    assert audit["multi_path_write_events"] == 0
    assert audit["max_paths_per_write"] == 1
    assert audit["covered_paths"] == ["step_001.txt", "step_002.txt"]


def test_replay_write_audit_records_and_accepts_one_multi_artifact_write_event():
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "bulk",
                "type": "command_execution",
                "command": "printf a > step_001.txt; printf b > step_002.txt",
            },
        }
    ]

    audit = audit_replay_write_events(events, {"step_001.txt", "step_002.txt"})

    assert audit["followed"] is True
    assert audit["write_events"] == 1
    assert audit["multi_path_write_events"] == 1
    assert audit["max_paths_per_write"] == 2
    assert audit["violations"] == []


def test_replay_write_audit_is_unavailable_without_native_events():
    audit = audit_replay_write_events([], {"step_001.txt"})

    assert audit["available"] is False
    assert audit["followed"] is False


def test_clean_recovery_has_no_fault_or_missing_artifacts(tmp_path):
    plan = build_extreme_plan()
    assert rollback_targets(plan, "clean_recovery") == set()
    assert prepare(plan, "clean_recovery", tmp_path) == []
    assert all((tmp_path / step.relative_path).is_file() for step in plan.steps)
    manifest = make_manifest(plan, [], fault_injected=False)
    assert manifest["fault_step_id"] is None
    assert manifest["missing"] == []
    assert manifest["repair"] == []


def test_crash_without_rollback_retains_faults_without_oracle_repair_hint(tmp_path):
    plan = build_extreme_plan()
    assert rollback_targets(plan, "crash_no_rollback") == set()
    assert prepare(plan, "crash_no_rollback", tmp_path) == []
    for index in plan.oracle_closure:
        content = (tmp_path / plan.steps[index].relative_path).read_text()
        assert content == injected_stale_payload(index, len(plan.steps))
        assert content != desired(index)
        assert "FAULTY" not in content
    manifest = make_manifest(
        plan,
        [],
        diagnosis_required=True,
    )
    assert manifest["missing"] == []
    assert manifest["repair"] == []
    assert manifest["fault_step_id"] is None
    assert manifest["diagnosis_required"] is True


def test_every_fault_policy_localizes_from_the_same_unknown_crash_state(tmp_path):
    plan = build_extreme_plan()
    expected_faults = set(plan.oracle_closure)
    expected_targets = {
        "crash_no_rollback": set(),
        "turn_checkpoint_proxy": set(range(len(plan.steps))),
        "temporal_step": set(range(min(expected_faults), len(plan.steps))),
        "whole_branch": set(range(len(plan.steps))),
        "agenttx_causal": expected_faults,
        "ideal_oracle": expected_faults,
    }

    for policy, expected in expected_targets.items():
        workspace = tmp_path / policy
        missing, localized, targets = prepare_recovery(plan, policy, workspace)

        assert localized == expected_faults
        assert targets == expected
        assert missing == sorted(expected)


def test_contract_localizer_does_not_need_fault_truth(tmp_path):
    plan = build_extreme_plan()
    for step in plan.steps:
        (tmp_path / step.relative_path).write_text(
            f"artifact-{step.index:03d}-valid\n",
            encoding="utf-8",
        )
    (tmp_path / plan.steps[73].relative_path).write_text("corrupt\n", encoding="utf-8")

    localized = localize_artifact_faults(plan, tmp_path)

    assert localized == {73}
    assert recovery_targets_from_localization(plan, "agenttx_causal", localized) == {73}
