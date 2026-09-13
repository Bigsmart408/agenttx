#!/usr/bin/env python3
"""Native SWEAgent workload backend for shared experiment runners."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
import tomllib
import traceback
import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Optional

import yaml
from swerex.deployment.config import LocalDeploymentConfig, get_deployment

from agenttx.agents.external import ExternalHarnessResult
from agenttx.agents.swe_agent import create_swe_agent_adapter
from sweagent.agent.agents import DefaultAgentConfig, get_agent_from_config
from sweagent.agent.problem_statement import TextProblemStatement
from sweagent.environment.swe_env import SWEEnv


DEFAULT_SWE_AGENT_CONFIG = Path("/home/bfq/SWE-agent/config/bash_only.yaml")
DEFAULT_SWE_AGENT_ROOT = Path("/home/bfq/SWE-agent")
DEFAULT_CODEX_CONFIG = Path("/home/bfq/.codex/config.toml")
DEFAULT_CODEX_AUTH = Path("/home/bfq/.codex/auth.json")
_RETIRED_DEPLOYMENTS: list[object] = []


@dataclass(frozen=True)
class CodexGPTProfile:
    """Non-secret model settings mirrored from this user's Codex config."""

    model: str
    api_base: str
    reasoning_effort: str
    provider: str
    wire_api: str
    disable_response_storage: bool
    api_key: str = field(repr=False, compare=False)

    @property
    def litellm_model(self) -> str:
        return self.model if "/" in self.model else f"openai/{self.model}"

    def public_dict(self) -> dict:
        return {
            "model": self.model,
            "litellm_model": self.litellm_model,
            "api_base": self.api_base,
            "reasoning_effort": self.reasoning_effort,
            "provider": self.provider,
            "wire_api": self.wire_api,
            "disable_response_storage": self.disable_response_storage,
        }


def _openai_api_base(value: str) -> str:
    base = value.rstrip("/")
    return base if base.endswith("/v1") else f"{base}/v1"


def load_codex_gpt_profile(
    config_path: Path = DEFAULT_CODEX_CONFIG,
    auth_path: Path = DEFAULT_CODEX_AUTH,
    *,
    model_override: Optional[str] = None,
) -> CodexGPTProfile:
    """Load the GPT endpoint exactly once without logging or hashing its key."""
    with config_path.open("rb") as handle:
        config = tomllib.load(handle)
    auth = json.loads(auth_path.read_text(encoding="utf-8"))

    provider_name = str(config.get("model_provider") or "OpenAI")
    providers = config.get("model_providers") or {}
    provider_data = next(
        (
            value
            for name, value in providers.items()
            if str(name).lower() == provider_name.lower()
        ),
        None,
    )
    if not isinstance(provider_data, dict):
        raise ValueError(f"Codex provider {provider_name!r} is not configured")
    api_key = str(auth.get("OPENAI_API_KEY") or "")
    if not api_key:
        raise ValueError(f"OPENAI_API_KEY is absent from {auth_path}")
    base_url = str(provider_data.get("base_url") or "")
    if not base_url:
        raise ValueError(f"base_url is absent for Codex provider {provider_name!r}")
    model = model_override or str(config.get("model") or "")
    if not model:
        raise ValueError(f"model is absent from {config_path}")
    return CodexGPTProfile(
        model=model,
        api_base=_openai_api_base(base_url),
        reasoning_effort=str(config.get("model_reasoning_effort") or "high"),
        provider=provider_name,
        wire_api=str(provider_data.get("wire_api") or "responses"),
        disable_response_storage=bool(config.get("disable_response_storage", False)),
        api_key=api_key,
    )


def _resolve_bundle_paths(agent_data: dict, swe_agent_root: Path) -> None:
    tools = agent_data.get("tools") or {}
    for bundle in tools.get("bundles") or []:
        value = bundle.get("path") if isinstance(bundle, dict) else None
        if not value:
            continue
        path = Path(str(value))
        if not path.is_absolute():
            bundle["path"] = str((swe_agent_root / path).resolve())


def load_agent_config(
    config_path: Path,
    *,
    profile: CodexGPTProfile,
    swe_agent_root: Path = DEFAULT_SWE_AGENT_ROOT,
    max_model_calls: int = 30,
) -> tuple[DefaultAgentConfig, str]:
    """Apply the Codex GPT profile to SWEAgent's native model config."""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("agent"), dict):
        raise ValueError(f"SWEAgent config has no agent mapping: {config_path}")
    agent_data = dict(raw["agent"])
    _resolve_bundle_paths(agent_data, swe_agent_root)
    model_data = dict(agent_data.get("model") or {})
    completion_kwargs = dict(model_data.get("completion_kwargs") or {})
    completion_kwargs["reasoning_effort"] = profile.reasoning_effort
    if profile.disable_response_storage:
        completion_kwargs["store"] = False
        allowed = set(completion_kwargs.get("allowed_openai_params") or [])
        allowed.add("store")
        completion_kwargs["allowed_openai_params"] = sorted(allowed)
    model_data.update(
        {
            "name": profile.litellm_model,
            "api_base": profile.api_base,
            "api_key": "$OPENAI_API_KEY",
            "per_instance_cost_limit": 0.0,
            "total_cost_limit": 0.0,
            "per_instance_call_limit": max_model_calls,
            # LiteLLM rejects temperature=0 and top_p for GPT-5 models.
            "temperature": 1.0,
            "top_p": None,
            "completion_kwargs": completion_kwargs,
        }
    )
    agent_data["model"] = model_data
    config = DefaultAgentConfig.model_validate(agent_data)
    canonical = json.dumps(config.model_dump(mode="json"), sort_keys=True)
    return config, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _cleanup_sweagent_runtime() -> None:
    shutil.rmtree("/root/tools", ignore_errors=True)
    Path("/root/state.json").unlink(missing_ok=True)
    Path("/root/.swe-agent-env").unlink(missing_ok=True)


def _initialize_workspace_repo(workspace: Path) -> None:
    subprocess.run(["git", "init", "--quiet"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=AgentTX Experiment",
            "-c",
            "user.email=agenttx@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "M3 recovery baseline",
        ],
        cwd=workspace,
        check=True,
    )


@dataclass(frozen=True)
class SWEAgentHarnessResult(ExternalHarnessResult):
    metadata: dict = field(default_factory=dict)


class SWEAgentNativeWorkloadHarness:
    """Expose SWEAgent's own DefaultAgent loop through the M3 harness seam."""

    name = "sweagent_native"

    def __init__(
        self,
        *,
        output_dir: Path,
        profile: CodexGPTProfile,
        config_path: Path = DEFAULT_SWE_AGENT_CONFIG,
        swe_agent_root: Path = DEFAULT_SWE_AGENT_ROOT,
        max_steps: int = 30,
        max_model_calls: int = 30,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.profile = profile
        self.model = profile.model
        self.max_steps = max_steps
        self.config, self.config_hash = load_agent_config(
            config_path,
            profile=profile,
            swe_agent_root=swe_agent_root,
            max_model_calls=max_model_calls,
        )
        # The config stores only the environment variable name, never this value.
        os.environ["OPENAI_API_KEY"] = profile.api_key

    @property
    def public_config(self) -> dict:
        return {
            "harness_backend": self.name,
            "native_adapter": "SWEAgentNativeAdapter",
            "sweagent_config_hash": self.config_hash,
            "codex_gpt_profile": self.profile.public_dict(),
            "gpt5_protocol_mapping": {
                "temperature": 1.0,
                "top_p": None,
            },
        }

    def run(
        self,
        task: str,
        workdir: Path,
        env: Optional[Dict[str, str]] = None,
    ) -> SWEAgentHarnessResult:
        if os.environ.get("AGENTTX_MOTIVATION_BWRAP") != "1":
            raise RuntimeError(
                "SWEAgent-native M3 must run in the dedicated bwrap namespace"
            )
        workdir = Path(workdir).resolve()
        run_env = env or {}
        cell_id = run_env.get("AGENTTX_CELL_ID", workdir.parent.name)
        cell_output = (
            self.output_dir
            / "trajectories"
            / cell_id
            / f"attempt-{uuid.uuid4().hex}"
        )
        session_dir = workdir.parent / "session"
        agent = None
        adapter = None
        swe_env = None
        started = time.perf_counter()
        _cleanup_sweagent_runtime()
        try:
            _initialize_workspace_repo(workdir)
            prompt = task + (
                "\n\nUse the shell tools provided by SWEAgent. Do not inspect retained "
                "files. After applying the manifest, call the submit tool and stop."
            )
            problem = TextProblemStatement(text=prompt, id=cell_id)
            agent = get_agent_from_config(self.config.model_copy(deep=True))
            swe_env = SWEEnv(
                deployment=get_deployment(LocalDeploymentConfig()),
                repo=None,
                post_startup_commands=[],
            )
            swe_env.start()
            swe_env.communicate(f"cd -- {workdir}", check="raise")
            swe_env.set_env_variables({"ROOT": str(workdir)})
            adapter = create_swe_agent_adapter(
                agent,
                workdir=workdir,
                session_dir=session_dir,
                trace_backend="strace",
                # SWEAgent's submit bundle writes model.patch under the bwrap
                # tmpfs /root; it is tooling output, not part of the workload.
                ephemeral_roots=(Path("/root"),),
            )
            result = adapter.run(
                swe_env,
                problem,
                cell_output,
                max_steps=self.max_steps,
                commit=True,
            )
            submitted = str(result.summary).startswith("submitted")
            return SWEAgentHarnessResult(
                harness=self.name,
                model=self.model,
                returncode=0 if submitted else 1,
                duration_s=time.perf_counter() - started,
                stderr="" if submitted else f"SWEAgent exit_status={result.summary}",
                prompt_tokens=result.prompt_tokens,
                completion_tokens=result.completion_tokens,
                total_tokens=result.total_tokens,
                tool_calls=result.tool_calls,
                usage_source=result.usage_source,
                metadata={
                    "harness_backend": self.name,
                    "sweagent_model": self.model,
                    "sweagent_config_hash": self.config_hash,
                    "sweagent_exit_status": result.summary,
                    "sweagent_model_calls": result.model_calls,
                    "native_adapter": "SWEAgentNativeAdapter",
                    "trajectory_dir": str(cell_output),
                },
            )
        except Exception as exc:
            stats = getattr(getattr(agent, "model", None), "stats", None)
            prompt_tokens = int(getattr(stats, "tokens_sent", 0) or 0)
            completion_tokens = int(getattr(stats, "tokens_received", 0) or 0)
            model_calls = int(getattr(stats, "api_calls", 0) or 0)
            return SWEAgentHarnessResult(
                harness=self.name,
                model=self.model,
                returncode=127,
                duration_s=time.perf_counter() - started,
                stderr="".join(
                    traceback.format_exception(type(exc), exc, exc.__traceback__)
                )[-12000:],
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                tool_calls=len(getattr(agent, "trajectory", []) or []),
                usage_source="sweagent_model_stats",
                metadata={
                    "harness_backend": self.name,
                    "sweagent_model": self.model,
                    "sweagent_config_hash": self.config_hash,
                    "sweagent_exit_status": type(exc).__name__,
                    "sweagent_model_calls": model_calls,
                    "native_adapter": "SWEAgentNativeAdapter",
                    "trajectory_dir": str(cell_output),
                },
            )
        finally:
            if adapter is not None:
                with suppress(Exception):
                    adapter.close(destroy=True)
            if swe_env is not None:
                with suppress(Exception):
                    swe_env.close()
                # SWE-ReX 1.4's deployment destructor creates an unawaited
                # coroutine after SWEEnv.close() clears the current event loop.
                # Retaining only the closed deployment avoids per-cell warnings
                # without retaining the trajectory or environment state.
                _RETIRED_DEPLOYMENTS.append(swe_env.deployment)
            with suppress(Exception):
                _cleanup_sweagent_runtime()
