import json
from pathlib import Path

import yaml

from experiments.scripts.sweagent_native_workload import (
    _initialize_workspace_repo,
    load_codex_gpt_profile,
    load_agent_config,
)
from experiments.scripts.bench_intra_turn_replay import make_manifest, make_prompt
from experiments.workloads.intra_turn_recovery_dag import build_intra_turn_plan


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
    prompt = make_prompt(payload)

    assert payload["missing"] == []
    assert payload["repair"] == []
    assert payload["fault_step_id"] is None
    assert payload["diagnosis_required"] is True
    assert plan.trace_id not in prompt
    assert all(step.relative_path not in prompt for step in plan.steps)
    assert "No fault location, repair targets, or expected contents are provided" in prompt
    assert "Inspect the workspace" in prompt
