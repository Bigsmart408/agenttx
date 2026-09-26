import csv
import json
from pathlib import Path

import yaml
import tiktoken

from experiments.scripts import sweagent_native_workload
from experiments.scripts.sweagent_native_workload import (
    _native_active_history_messages,
    _initialize_workspace_repo,
    _workload_suffix,
    load_codex_gpt_profile,
    load_agent_config,
)
from experiments.scripts.bench_intra_turn_replay import (
    FAULT_NOTICE,
    config_compat_value,
    make_manifest,
    make_prompt,
    render_unrewound_history,
    terminal_keys,
)
from experiments.workloads.intra_turn_recovery_dag import (
    build_intra_turn_plan,
    build_multiturn_extreme_plan,
)


def _write_codex_profile(tmp_path: Path):
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        'model_provider = "OpenAI"\n'
        'model = "gpt-5.6-sol"\n'
        'model_reasoning_effort = "high"\n'
        'disable_response_storage = true\n'
        '[model_providers.OpenAI]\n'
        'base_url = "http://api.example.test:8080"\n'
        'wire_api = "responses"\n',
        encoding="utf-8",
    )
    auth_path = tmp_path / "auth.json"
    auth_path.write_text(
        json.dumps({"OPENAI_API_KEY": "test-secret"}),
        encoding="utf-8",
    )
    return load_codex_gpt_profile(config_path, auth_path)


def test_codex_profile_matches_user_model_without_exposing_key(tmp_path: Path) -> None:
    profile = _write_codex_profile(tmp_path)

    assert profile.model == "gpt-5.6-sol"
    assert profile.litellm_model == "openai/gpt-5.6-sol"
    assert profile.api_base == "http://api.example.test:8080/v1"
    assert profile.reasoning_effort == "high"
    assert profile.disable_response_storage is True
    assert "test-secret" not in repr(profile)
    assert "test-secret" not in json.dumps(profile.public_dict())


def test_codex_profile_accepts_documented_experiment_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    auth_path = tmp_path / "auth.json"
    _write_codex_profile(tmp_path)

    profile = load_codex_gpt_profile(
        config_path,
        auth_path,
        model_override="gpt-5.6-luna",
        reasoning_effort_override="max",
    )

    assert profile.model == "gpt-5.6-luna"
    assert profile.litellm_model == "openai/gpt-5.6-luna"
    assert profile.reasoning_effort == "max"


def test_resume_reads_sweagent_reasoning_effort_from_nested_profile() -> None:
    codex_config = {"reasoning_effort": "max"}
    sweagent_config = {"codex_gpt_profile": {"reasoning_effort": "max"}}

    assert config_compat_value(codex_config, "reasoning_effort") == "max"
    assert config_compat_value(sweagent_config, "reasoning_effort") == "max"


def test_coarse_timeout_is_terminal_but_causal_timeout_is_retryable(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "raw.csv"
    rows = [
        {
            "trace_id": "trace",
            "policy": "temporal_step",
            "repeat": 0,
            "completed": False,
            "harness_returncode": 124,
        },
        {
            "trace_id": "trace",
            "policy": "agenttx_causal",
            "repeat": 0,
            "completed": False,
            "harness_returncode": 124,
        },
    ]
    with raw_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    assert terminal_keys(raw_path) == {("trace", "temporal_step", 0)}
    assert terminal_keys(raw_path, rerun_completed=True) == {
        ("trace", "temporal_step", 0)
    }


def test_load_agent_config_uses_codex_gpt_profile_and_absolute_bundles(
    tmp_path: Path,
) -> None:
    swe_root = Path("/home/bfq/SWE-agent")
    bundle = swe_root / "tools" / "submit"
    assert (bundle / "config.yaml").is_file()
    config_path = tmp_path / "agent.yaml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "agent": {
                    "type": "default",
                    "templates": {
                        "system_template": "system",
                        "instance_template": "{{problem_statement}}",
                    },
                    "tools": {
                        "bundles": [{"path": "tools/submit"}],
                        "parse_function": {"type": "single_bash_code_block"},
                    },
                    "model": {"name": "placeholder"},
                }
            }
        ),
        encoding="utf-8",
    )

    profile = _write_codex_profile(tmp_path)
    config, config_hash = load_agent_config(
        config_path,
        profile=profile,
        swe_agent_root=swe_root,
        max_model_calls=7,
    )

    assert config.model.name == "openai/gpt-5.6-sol"
    assert config.model.api_base == "http://api.example.test:8080/v1"
    assert config.model.api_key.get_secret_value() == "$OPENAI_API_KEY"
    assert config.model.temperature == 1.0
    assert config.model.top_p is None
    assert config.model.completion_kwargs["reasoning_effort"] == "high"
    assert config.model.completion_kwargs["store"] is False
    assert config.model.completion_kwargs["allowed_openai_params"] == ["store"]
    assert config.model.per_instance_call_limit == 7
    assert config.tools.bundles[0].path == bundle.resolve()
    assert len(config_hash) == 64


def test_initialize_workspace_repo_creates_submit_compatible_baseline(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "retained.txt").write_text("valid\n", encoding="utf-8")

    _initialize_workspace_repo(workspace)

    assert (workspace / ".git").is_dir()


def test_initialize_workspace_repo_allows_empty_recovery_view(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    _initialize_workspace_repo(workspace)

    assert (workspace / ".git").is_dir()


def test_crash_no_rollback_manifest_discloses_no_repair_or_fault_location() -> None:
    plan = build_intra_turn_plan(
        16,
        fault_position=0.5,
        closure_ratio=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )

    payload = make_manifest(
        plan,
        [],
        fault_injected=True,
        diagnosis_required=True,
    )
    history = render_unrewound_history(plan)
    prompt = make_prompt(
        payload,
        policy="crash_no_rollback",
        active_history=history,
    )

    assert payload["missing"] == []
    assert payload["repair"] == []
    assert payload["fault_step_id"] is None
    assert payload["diagnosis_required"] is True
    assert plan.trace_id not in prompt
    assert all(step.relative_path not in history for step in plan.steps)
    assert "FAULTY" not in prompt
    assert FAULT_NOTICE in prompt
    assert "Fixed pre-crash turn receipts" in prompt
    assert "active_unrewound" in prompt
    assert "at most one indexed step" in prompt
    assert "step_" not in prompt
    assert "path_template" not in prompt
    assert "content_template" not in prompt


def test_crash_no_rollback_history_maps_to_native_sweagent_messages() -> None:
    plan = build_intra_turn_plan(
        16,
        fault_position=0.5,
        closure_ratio=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )

    messages = _native_active_history_messages(render_unrewound_history(plan), "main")

    assert len(messages) == 2 * len(plan.steps)
    assert [message["role"] for message in messages] == [
        role for _ in plan.steps for role in ("assistant", "user")
    ]
    assert all(message["agent"] == "main" for message in messages)
    assert all("step_" not in message["content"] for message in messages)
    assert all("FAULTY" not in message["content"] for message in messages)
    assert all("fault" not in message["content"].lower() for message in messages)
    assert messages[0]["action"] == "./workload.py 0"
    assert messages[-2]["action"] == "./workload.py 15"


def test_recovery_prompt_uses_compact_state_without_fault_location_or_history() -> None:
    plan = build_intra_turn_plan(
        16,
        fault_position=0.5,
        closure_ratio=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )
    payload = make_manifest(
        plan,
        [],
        fault_injected=True,
        diagnosis_required=True,
        detected_fault_step_ids=[plan.fault_step_id],
    )

    prompt = make_prompt(payload, policy="agenttx_causal")

    assert FAULT_NOTICE in prompt
    assert "Continue the original task" in prompt
    assert "Active pre-crash tool history" not in prompt
    assert payload["fault_step_id"] is None
    assert payload["missing"] == []
    assert "step_" not in prompt
    assert "path_template" not in prompt
    assert "content_template" not in prompt


def test_coarse_fault_prompts_name_policy_without_disclosing_location() -> None:
    plan = build_intra_turn_plan(
        16,
        fault_position=0.5,
        closure_ratio=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )
    payload = make_manifest(plan, [], fault_injected=True, diagnosis_required=True)

    prompts = {
        policy: make_prompt(payload, policy=policy)
        for policy in ("turn_checkpoint_proxy", "temporal_step", "whole_branch")
    }

    assert len(set(prompts.values())) == 3
    assert "turn checkpoint" in prompts["turn_checkpoint_proxy"]
    assert "do not check individual operations" in prompts["turn_checkpoint_proxy"]
    assert "temporal suffix" in prompts["temporal_step"]
    assert "directly repair the full suffix" in prompts["temporal_step"]
    assert "whole branch" in prompts["whole_branch"]
    assert "Do not diagnose the fault location" in prompts["whole_branch"]
    for prompt in prompts.values():
        assert "invalid effect(s)" not in prompt
        assert "originated at original step" not in prompt
        assert "repair plan" not in prompt.lower()
        assert "path_template" not in prompt
        assert "content_template" not in prompt


def test_agenttx_prompt_discloses_only_localizer_output() -> None:
    plan = build_intra_turn_plan(
        16,
        fault_position=0.5,
        closure_ratio=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )
    detected = [plan.fault_step_id]
    payload = make_manifest(
        plan,
        [],
        fault_injected=True,
        diagnosis_required=True,
        detected_fault_step_ids=detected,
    )

    prompt = make_prompt(payload, policy="agenttx_causal")

    assert f"AgentTX localization found invalid effect(s) at t0/o{plan.fault_step_id}" in prompt
    assert "oracle_fault_step_id" not in prompt
    assert "oracle_closure" not in prompt
    assert "path_template" not in prompt
    assert "content_template" not in prompt


def test_ideal_oracle_discloses_only_the_exact_fault_step() -> None:
    plan = build_intra_turn_plan(
        16,
        fault_position=0.5,
        closure_ratio=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )
    payload = make_manifest(
        plan,
        [],
        fault_injected=True,
        diagnosis_required=True,
        oracle_fault_step_id=plan.fault_step_id,
    )

    prompt = make_prompt(payload, policy="ideal_oracle")

    assert f"Oracle invalid effect(s) are t0/o{plan.fault_step_id}" in prompt
    assert "oracle_closure" not in prompt
    assert "affected artifact paths" not in prompt
    assert "path_template" not in prompt
    assert "content_template" not in prompt


def test_unrewound_history_contains_all_calls_but_no_oracle_fault_labels() -> None:
    plan = build_intra_turn_plan(
        16,
        fault_position=0.5,
        closure_ratio=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )

    history = render_unrewound_history(plan)
    parsed = json.loads(history)

    assert len(parsed["turns"]) == len(plan.steps)
    assert parsed["status"] == "active_unrewound"
    assert parsed["schema"] == "agenttx.active_tool_history/v2"
    assert all(turn["tool_result"]["exit_code"] == 0 for turn in parsed["turns"])
    assert all(
        "completed successfully" in turn["assistant_message"]
        for turn in parsed["turns"]
    )
    assert all(
        turn["assistant_tool_call"]["arguments"]["cmd"][0] == "./workload.py"
        for turn in parsed["turns"]
    )
    assert "step_" not in history
    assert "fault_step_id" not in history
    assert "oracle_closure" not in history
    assert "FAULTY" not in history
    assert "intended_content_sha256" not in history


def test_every_fault_policy_uses_the_same_opaque_diagnosis_contract() -> None:
    plan = build_intra_turn_plan(
        16,
        fault_position=0.5,
        closure_ratio=0.25,
        shape="layered",
        interleaving="alternating",
        seed=0,
    )
    serialized = []
    for policy_missing in ([], [1], [1, 2, 3], list(range(16))):
        payload = make_manifest(
            plan,
            policy_missing,
            fault_injected=True,
            diagnosis_required=True,
        )
        assert payload["fault_step_id"] is None
        assert payload["missing"] == []
        assert payload["repair"] == []
        assert "retained_count" not in payload
        serialized.append(json.dumps(payload, sort_keys=True))

    assert len(set(serialized)) == 1


def test_sweagent_workload_suffix_allows_only_self_diagnosis_to_inspect() -> None:
    diagnosis = _workload_suffix(True)
    replay = _workload_suffix(False)

    assert "continue the original task" in diagnosis
    clean = _workload_suffix(False, clean_baseline=True)
    assert "Do not use shell tools" in clean
    assert "submit tool immediately" in clean
    assert "fault location" not in diagnosis
    assert "path-specific" not in diagnosis
    assert "original task" in replay


def test_sweagent_clean_baseline_has_single_step_guard() -> None:
    source = Path(sweagent_native_workload.__file__).read_text(encoding="utf-8")
    assert "max_steps=1 if clean_baseline else self.max_steps" in source
    assert "Return exactly one bash code block containing only: submit" in source


def test_multiturn_fault_prompts_have_equal_cost_and_shared_receipts() -> None:
    plan = build_multiturn_extreme_plan()
    history = render_unrewound_history(plan)
    parsed = json.loads(history)
    encoding = tiktoken.get_encoding("o200k_base")
    prompts = {}

    for policy in (
        "clean_recovery",
        "crash_no_rollback",
        "turn_checkpoint_proxy",
        "temporal_step",
        "whole_branch",
        "agenttx_causal",
        "ideal_oracle",
    ):
        payload = make_manifest(
            plan,
            [],
            fault_injected=policy != "clean_recovery",
            diagnosis_required=policy != "clean_recovery",
            detected_fault_step_ids=(
                list(plan.oracle_closure) if policy == "agenttx_causal" else None
            ),
            oracle_fault_step_ids=(
                list(plan.oracle_closure) if policy == "ideal_oracle" else None
            ),
        )
        prompts[policy] = make_prompt(
            payload,
            policy=policy,
            active_history=history,
        )

    assert parsed["schema"] == "agenttx.active_turn_history/v1"
    assert len(parsed["turns"]) == 8
    assert len(_native_active_history_messages(history, "main")) == 16
    assert {len(encoding.encode(prompt)) for prompt in prompts.values()} == {547}
    assert "No crash was injected" in prompts["clean_recovery"]
    assert "invalid effect(s)" not in prompts["crash_no_rollback"]
    assert "invalid effect(s)" not in prompts["turn_checkpoint_proxy"]
    assert "invalid effect(s)" not in prompts["temporal_step"]
    assert "invalid effect(s)" not in prompts["whole_branch"]
    for step_id in plan.oracle_closure:
        step = plan.steps[step_id]
        point = f"t{step.turn_index}/o{step.turn_step_index}"
        assert point in prompts["agenttx_causal"]
        assert point in prompts["ideal_oracle"]
