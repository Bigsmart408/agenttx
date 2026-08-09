import argparse

import pytest

from experiments.scripts.bench_causal_retention import build_configs, score_retention
from experiments.workloads.causal_retention_dag import (
    SHAPES,
    build_causal_retention_plan,
)


@pytest.mark.parametrize("shape", SHAPES)
def test_causal_retention_plan_partitions_work(shape):
    plan = build_causal_retention_plan(
        24,
        shape=shape,
        fault_fraction=0.25,
        independent_fraction=0.5,
    )

    expected = set(plan.expected_rollback_ids)
    independent = set(plan.independent_ids)
    assert len(plan.steps) == 24
    assert plan.fault_step_id in expected
    assert expected.isdisjoint(independent)
    assert expected | independent == set(range(24))
    assert {index for index, step in enumerate(plan.steps) if step.role == "dependent"} <= expected
    assert len({step.name for step in plan.steps}) == 24
    assert len({step.relative_path for step in plan.steps}) == 24


def test_retention_score_distinguishes_recovery_policies():
    plan = build_causal_retention_plan(16)
    expected = set(plan.expected_rollback_ids)
    independent = set(plan.independent_ids)

    causal = score_retention(plan, expected, independent)
    assert causal["rollback_precision"] == 1.0
    assert causal["rollback_recall"] == 1.0
    assert causal["independent_retention"] == 1.0
    assert causal["target_removed"] == 1.0
    assert causal["final_correct"]

    temporal_targets = set(range(plan.fault_step_id, plan.total_steps))
    temporal_present = set(range(plan.fault_step_id))
    temporal = score_retention(plan, temporal_targets, temporal_present)
    assert temporal["rollback_recall"] == 1.0
    assert temporal["rollback_precision"] < 1.0
    assert temporal["independent_retention"] < 1.0
    assert not temporal["final_correct"]

    missing_dependencies = score_retention(
        plan,
        {plan.fault_step_id},
        set(range(plan.total_steps)) - {plan.fault_step_id},
    )
    assert missing_dependencies["rollback_precision"] == 1.0
    assert missing_dependencies["rollback_recall"] < 1.0
    assert missing_dependencies["invalid_work_retained"] > 0
    assert not missing_dependencies["final_correct"]


def test_sweep_selection_limits_generated_configs():
    args = argparse.Namespace(
        sweeps=["size"],
        sizes=[8, 16],
        fixed_size=32,
        shapes=list(SHAPES),
        fault_fractions=[0.1, 0.5],
        independent_fractions=[0.25, 0.75],
        default_fault_fraction=0.25,
        default_independent_fraction=0.5,
    )
    configs = build_configs(args)
    assert [config["sweep"] for config in configs] == ["size", "size"]
    assert [config["requested_total_steps"] for config in configs] == [8, 16]
