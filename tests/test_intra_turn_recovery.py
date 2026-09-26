from types import SimpleNamespace

from agenttx.conversation import ConversationLog
from agenttx.ledger import Effect, EffectKind, Ledger
from experiments.scripts.analyze_intra_turn_traces import closure, components
from experiments.scripts.bench_intra_turn_recovery import (
    analytical_rows,
    policy_targets,
    rewind_conversation_exact,
)
from experiments.scripts.bench_intra_turn_replay import (
    audit_replay_write_events,
    bootstrap_mean_ci,
    desired,
    desired_for_plan,
    injected_stale_payload,
    install_step_runner,
    localize_artifact_faults,
    make_manifest,
    prepare,
    prepare_recovery,
    replay_cases,
    recovery_targets_from_localization,
    rollback_targets,
    run_cell,
)
from experiments.workloads.intra_turn_recovery_dag import (
    INTERLEAVINGS,
    SHAPES,
    build_extreme_plan,
    build_intra_turn_plan,
    build_multi_turn_plan,
    build_multiturn_extreme_plan,
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


def test_dependency_value_grid_fixes_fault_before_realizing_density():
    values = tuple(index / 20 for index in range(21))
    for width in (8, 16, 64, 128):
        for fault in (0.10, 0.25, 0.50, 0.75, 0.90):
            expected_root = round((width - 1) * fault)
            suffix_size = width - expected_root - 1
            for value in values:
                plan = build_intra_turn_plan(
                    width,
                    fault_position=fault,
                    dependency_value=value,
                    shape="layered",
                    interleaving="alternating",
                )
                expected_dependents = round(suffix_size * value)
                assert plan.fault_step_id == expected_root
                assert len(plan.oracle_closure) == 1 + expected_dependents
                assert plan.requested_dependency_value == value
                assert plan.actual_dependency_value == (
                    expected_dependents / suffix_size if suffix_size else 0.0
                )


def test_dependency_value_and_legacy_closure_ratio_are_mutually_exclusive():
    import pytest

    kwargs = {
        "width": 64,
        "fault_position": 0.25,
        "shape": "layered",
        "interleaving": "alternating",
    }
    with pytest.raises(ValueError, match="exactly one"):
        build_intra_turn_plan(**kwargs)
    with pytest.raises(ValueError, match="exactly one"):
        build_intra_turn_plan(
            **kwargs,
            closure_ratio=0.25,
            dependency_value=0.25,
        )


def test_dependency_sweep_seeds_change_dag_without_changing_density():
    plans = [
        build_intra_turn_plan(
            64,
            fault_position=0.25,
            dependency_value=0.25,
            shape="layered",
            interleaving="alternating",
            seed=seed,
        )
        for seed in range(5)
    ]
    assert len({plan.manifest_hash for plan in plans}) == len(plans)
    assert len({plan.fault_step_id for plan in plans}) == 1
    assert len({len(plan.oracle_closure) for plan in plans}) == 1
    assert len({plan.actual_dependency_value for plan in plans}) == 1
    parent_layouts = {
        tuple(step.parent_indices for step in plan.steps) for plan in plans
    }
    assert len(parent_layouts) > 1


def test_multiturn_policy_boundaries_are_distinct_and_safe():
    plan = build_multi_turn_plan(
        5,
        16,
        fault_position=0.25,
        dependency_value=0.25,
        shape="layered",
        interleaving="alternating",
        seed=3,
    )

    checkpoint = policy_targets(plan, "turn_checkpoint_proxy")
    temporal = policy_targets(plan, "temporal_step")
    whole = policy_targets(plan, "whole_branch")
    ideal = policy_targets(plan, "ideal_oracle")

    assert plan.turn_count == 5
    assert ideal < checkpoint < whole
    assert ideal < temporal < whole
    assert checkpoint != temporal
    assert set(plan.oracle_closure) <= checkpoint
    assert set(plan.oracle_closure) <= temporal


def test_exact_set_baseline_does_not_expand_conversation_dependencies():
    ledger = Ledger()
    for step_id in range(2):
        ledger.add_step(
            f"step-{step_id}",
            [Effect(f"/artifact-{step_id}", EffectKind.WRITE)],
        )
    conversation = ConversationLog()
    conversation.seed("system", "request")
    conversation.append_turn(
        [
            {
                "role": "assistant",
                "content": "run two tools",
                "tool_calls": [
                    {
                        "id": f"call-{step_id}",
                        "type": "function",
                        "function": {"name": f"step-{step_id}", "arguments": "{}"},
                    }
                    for step_id in range(2)
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "call-0",
                "content": "root",
            },
            {
                "role": "tool",
                "tool_call_id": "call-1",
                "content": "dependent",
            },
        ],
        step_ids=[0, 1],
        tool_names=["step-0", "step-1"],
        kind="effect",
    )
    conversation.turns[-1].calls[1].parents = (0,)
    tx = SimpleNamespace(ledger=ledger, conversation=conversation)

    rewind_conversation_exact(tx, [0], mode="root_only")

    assert conversation.active_step_ids() == [1]
    assert conversation.generation == 1


def test_coarse_rollback_is_safe_but_requires_replay_for_exact_state():
    plan = build_intra_turn_plan(
        64,
        fault_position=0.25,
        dependency_value=0.25,
        shape="layered",
        interleaving="alternating",
    )
    rows = {row["policy"]: row for row in analytical_rows(plan, repeat=0)}

    for policy in ("turn_checkpoint_proxy", "temporal_step", "whole_branch"):
        assert rows[policy]["rollback_safe"] is True
        assert rows[policy]["exact_state_before_replay"] is False
        assert rows[policy]["useful_steps_wasted"] > 0
    assert rows["ideal_oracle"]["rollback_safe"] is True
    assert rows["ideal_oracle"]["exact_state_before_replay"] is True
    assert rows["no_recovery"]["rollback_safe"] is False


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
    assert manifest["schema"] == "agenttx.motivation.replay/multiturn-v1"
    assert manifest["artifact_contract"]["replay_unit"] == (
        "one artifact per write-producing tool invocation"
    )


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
    assert audit["covered_paths"] == ["step_001.txt", "step_002.txt"]


def test_replay_write_audit_recognizes_original_step_runner_calls():
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "runner-1",
                "type": "command_execution",
                "command": "./workload.py 1",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "id": "runner-2",
                "type": "command_execution",
                "command": "python workload.py 2",
            },
        },
    ]

    audit = audit_replay_write_events(events, {"step_001.txt", "step_002.txt"})

    assert audit["followed"] is True
    assert audit["covered_paths"] == ["step_001.txt", "step_002.txt"]


def test_replay_write_audit_normalizes_codex_command_argv():
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "runner-argv",
                "type": "command_execution",
                "command": ["./workload.py", "32"],
            },
        }
    ]

    audit = audit_replay_write_events(events, {"step_032.txt"})

    assert audit["followed"] is True
    assert audit["completed_commands"] == ["./workload.py 32"]


def test_replay_write_audit_recognizes_codex_shell_wrapped_runner_call():
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": "runner-shell",
                "type": "command_execution",
                "command": "/bin/bash -lc './workload.py 64'",
            },
        }
    ]

    audit = audit_replay_write_events(events, {"step_064.txt"})

    assert audit["followed"] is True
    assert audit["covered_paths"] == ["step_064.txt"]


def test_original_step_runner_is_retained_and_writes_one_step(tmp_path):
    plan = build_extreme_plan()
    install_step_runner(plan, tmp_path)

    import subprocess

    subprocess.run([str(tmp_path / "workload.py"), "32"], cwd=tmp_path, check=True)

    assert (tmp_path / "step_032.txt").read_text(encoding="utf-8") == desired(32)
    assert not (tmp_path / "step_031.txt").exists()


def test_replay_write_audit_rejects_one_multi_artifact_write_event():
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

    assert audit["followed"] is False
    assert audit["write_events"] == 1
    assert audit["violations"] == [
        {"item_id": "bulk", "paths": ["step_001.txt", "step_002.txt"]}
    ]


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


def test_only_agenttx_localizes_and_coarse_policies_keep_location_hidden(tmp_path):
    plan = build_extreme_plan()
    expected_faults = set(plan.oracle_closure)
    expected_targets = {
        "no_recovery": set(),
        "root_only": set(plan.fault_roots),
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

        assert localized == (
            expected_faults if policy == "agenttx_causal" else set()
        )
        assert targets == expected
        assert missing == sorted(expected)
        if policy in {"root_only", "agenttx_causal", "ideal_oracle"}:
            assert all(
                not (workspace / plan.steps[index].relative_path).exists()
                for index in expected
            )
        else:
            assert all(
                (workspace / plan.steps[index].relative_path).exists()
                for index in range(len(plan.steps))
            )


def test_replay_suite_uses_all_registered_motivation_manifests():
    cases = replay_cases("motivation400")

    assert len(cases) == 400
    assert len({plan.trace_id for _, plan in cases}) == 400
    assert {suite_slice for suite_slice, _ in cases} == {
        "mb2_task_length",
        "mb3_fault_position",
        "mb4_dependency",
    }


def test_motivation_policies_complete_or_fail_by_boundary_as_expected():
    plan = build_multi_turn_plan(
        2,
        8,
        fault_position=0.25,
        dependency_value=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )

    class PolicyHarness:
        name = "test"
        model = "test"

        def __init__(self, policy):
            self.policy = policy

        def run(self, task, workdir, env=None):
            targets = rollback_targets(plan, self.policy)
            commands = []
            if self.policy in {"root_only", "turn_checkpoint_proxy", "temporal_step"}:
                commands.extend(
                    f"./workload.py check-turn {turn}" for turn in plan.turn_ids
                )
            if self.policy == "root_only":
                commands.extend(
                    f"./workload.py check-step {step.index}" for step in plan.steps
                )
            elif self.policy == "temporal_step":
                earliest_turn = min(plan.steps[index].turn_index for index in targets)
                commands.extend(
                    f"./workload.py check-step {step.index}"
                    for step in plan.steps
                    if step.turn_index == earliest_turn
                )
            for index in sorted(targets):
                path = workdir / plan.steps[index].relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(desired_for_plan(plan, index), encoding="utf-8")
                commands.append(f"./workload.py repair {index}")
            events = [
                {
                    "type": "item.completed",
                    "item": {
                        "id": f"command-{index}",
                        "type": "command_execution",
                        "command": command,
                    },
                }
                for index, command in enumerate(commands)
            ]
            return SimpleNamespace(
                prompt_tokens=10,
                completion_tokens=10,
                total_tokens=20,
                usage_source="test",
                tool_calls=len(commands),
                returncode=0,
                duration_s=0.01,
                stderr="",
                events=events,
                metadata={},
            )

    expected = {
        "clean_recovery": True,
        "no_recovery": False,
        "root_only": False,
        "turn_checkpoint_proxy": True,
        "temporal_step": True,
        "whole_branch": True,
        "ideal_oracle": True,
    }
    rows = {
        policy: run_cell(plan, policy, 0, PolicyHarness(policy))
        for policy in expected
    }

    assert {policy: row["verifier_passed"] for policy, row in rows.items()} == expected
    assert all(row["manifest_hash"] == plan.manifest_hash for row in rows.values())
    assert all(row["policy_handoff_tokens"] in {0, 72} for row in rows.values())


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


def test_multiturn_extreme_plan_distributes_faults_and_recovery_boundaries(tmp_path):
    plan = build_multiturn_extreme_plan()

    assert plan.turn_count == 8
    assert plan.fault_roots == (1, 34, 83, 116)
    assert plan.oracle_closure == (1, 17, 34, 51, 83, 95, 116, 127)
    assert {plan.steps[root].turn_index for root in plan.fault_roots} == {0, 2, 5, 7}
    assert all(step.relative_path.startswith("effects/") for step in plan.steps)
    assert all("step_" not in step.relative_path for step in plan.steps)

    expected_sizes = {
        "crash_no_rollback": 0,
        "turn_checkpoint_proxy": 96,
        "temporal_step": 127,
        "whole_branch": 128,
        "agenttx_causal": 8,
        "ideal_oracle": 8,
    }
    for policy, expected_size in expected_sizes.items():
        _, localized, targets = prepare_recovery(plan, policy, tmp_path / policy)
        assert localized == (
            set(plan.oracle_closure) if policy == "agenttx_causal" else set()
        )
        assert len(targets) == expected_size


def test_multiturn_no_rollback_requires_hierarchical_diagnostics():
    plan = build_multiturn_extreme_plan()
    required_paths = {
        plan.steps[index].relative_path for index in plan.oracle_closure
    }
    commands = [f"./workload.py check-turn {turn}" for turn in plan.turn_ids]
    commands.extend(
        f"./workload.py check-step {step.index}"
        for step in plan.steps
    )
    commands.extend(
        f"./workload.py repair {index}" for index in plan.oracle_closure
    )
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": f"command-{index}",
                "type": "command_execution",
                "command": command,
            },
        }
        for index, command in enumerate(commands)
    ]

    audit = audit_replay_write_events(
        events,
        required_paths,
        plan=plan,
        policy="crash_no_rollback",
    )

    assert audit["followed"] is True
    assert audit["diagnostic_protocol_followed"] is True
    assert len(audit["diagnostic_checked_turns"]) == 8
    assert len(audit["diagnostic_checked_steps"]) == 128


def test_replay_audit_rejects_writes_outside_policy_boundary():
    plan = build_multi_turn_plan(
        2,
        8,
        fault_position=0.25,
        dependency_value=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )
    root = plan.fault_roots[0]
    unauthorized = next(index for index in plan.oracle_closure if index != root)
    required_paths = {plan.steps[root].relative_path}
    commands = [f"./workload.py check-turn {turn}" for turn in plan.turn_ids]
    commands.extend(
        f"./workload.py check-step {step.index}" for step in plan.steps
    )
    commands.extend(
        [
            f"./workload.py repair {root}",
            f"./workload.py repair {unauthorized}",
        ]
    )
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": f"command-{index}",
                "type": "command_execution",
                "command": command,
            },
        }
        for index, command in enumerate(commands)
    ]

    audit = audit_replay_write_events(
        events,
        required_paths,
        plan=plan,
        policy="root_only",
    )

    assert audit["followed"] is False
    assert any(
        violation.get("reason") == "unauthorized_replay"
        for violation in audit["violations"]
    )

    bypass = audit_replay_write_events(
        [
            {
                "type": "item.completed",
                "item": {
                    "id": "bypass",
                    "type": "command_execution",
                    "command": "sed -n '1,240p' workload.py",
                },
            }
        ],
        required_paths,
        plan=plan,
        policy="crash_no_rollback",
    )
    assert bypass["followed"] is False
    assert bypass["diagnostic_violations"] == [
        {"item_id": "bypass", "reason": "opaque_state_bypass"}
    ]


def test_localized_policy_may_confirm_location_without_failing_audit():
    plan = build_multiturn_extreme_plan()
    required_paths = {
        plan.steps[index].relative_path for index in plan.oracle_closure
    }
    commands = ["./workload.py check-turn 0", "./workload.py check-step 1"]
    commands.extend(f"./workload.py repair {index}" for index in plan.oracle_closure)
    events = [
        {
            "type": "item.completed",
            "item": {
                "id": f"command-{index}",
                "type": "command_execution",
                "command": command,
            },
        }
        for index, command in enumerate(commands)
    ]

    audit = audit_replay_write_events(
        events,
        required_paths,
        plan=plan,
        policy="agenttx_causal",
    )

    assert audit["followed"] is True
    assert audit["diagnostic_protocol_followed"] is True
    assert audit["diagnostic_violations"] == []
