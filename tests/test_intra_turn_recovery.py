from experiments.scripts.analyze_intra_turn_traces import closure, components
from experiments.scripts.bench_intra_turn_replay import (
    bootstrap_mean_ci,
    make_manifest,
    prepare,
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
    assert [entry["path"] for entry in manifest["missing"]] == [
        plan.steps[index].relative_path for index in (1, 32, 64, 127)
    ]


def test_replay_incremental_bootstrap_accepts_no_complete_pairs():
    assert bootstrap_mean_ci([]) == (0.0, 0.0)


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
        assert "FAULTY" in (tmp_path / plan.steps[index].relative_path).read_text()
    manifest = make_manifest(
        plan,
        [],
        diagnosis_required=True,
    )
    assert manifest["missing"] == []
    assert manifest["repair"] == []
    assert manifest["fault_step_id"] is None
    assert manifest["diagnosis_required"] is True
