#!/usr/bin/env python3
"""Native SWEAgent workload backend for shared experiment runners."""

from __future__ import annotations

import hashlib
import json
import os
import signal
import shlex
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
from agenttx.agents.swe_agent import (
    SWEAgentIntegrationError,
    SWEAgentNativeAdapter,
    create_swe_agent_adapter,
)
from sweagent.agent.agents import DefaultAgentConfig, get_agent_from_config
from sweagent.agent.problem_statement import TextProblemStatement
from sweagent.environment.swe_env import SWEEnv


DEFAULT_SWE_AGENT_CONFIG = Path("/home/bfq/SWE-agent/config/bash_only.yaml")
DEFAULT_SWE_AGENT_ROOT = Path("/home/bfq/SWE-agent")
DEFAULT_CODEX_CONFIG = Path("/home/bfq/.codex/config.toml")
DEFAULT_CODEX_AUTH = Path("/home/bfq/.codex/auth.json")


class _SWEAgentWallTimeout(TimeoutError):
    pass


def _raise_wall_timeout(_signum, _frame) -> None:
    raise _SWEAgentWallTimeout("SWEAgent workload exceeded its wall-clock timeout")


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
    reasoning_effort_override: Optional[str] = None,
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
        reasoning_effort=(
            reasoning_effort_override
            or str(config.get("model_reasoning_effort") or "high")
        ),
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
    if (workspace / ".git").exists():
        return
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
            "--allow-empty",
            "-m",
            "M3 recovery baseline",
        ],
        cwd=workspace,
        check=True,
    )


def _workload_suffix(diagnosis_required: bool, *, clean_baseline: bool = False) -> str:
    if clean_baseline:
        return (
            "\n\nDo not use shell tools or inspect the workspace. Call the submit tool "
            "immediately with the verified-clean acknowledgement and stop."
        )
    if diagnosis_required:
        return (
            "\n\nUse the shell tools provided by SWEAgent and continue the original "
            "task from the current workspace. When the original task is complete, call "
            "the submit tool and stop."
        )
    return (
        "\n\nUse the shell tools provided by SWEAgent and continue the original task. "
        "When the original task is complete, call the submit tool and stop."
    )


def _native_active_history_messages(serialized: str, agent_name: str) -> list[dict]:
    """Convert the no-rollback projection into SWEAgent's native message shape."""
    payload = json.loads(serialized)
    if not isinstance(payload, dict) or payload.get("status") != "active_unrewound":
        raise ValueError("native no-rollback history must be active_unrewound")
    turns = payload.get("turns")
    if not isinstance(turns, list) or not turns:
        raise ValueError("native no-rollback history must contain turns")

    messages = []
    for turn in turns:
        if not isinstance(turn, dict):
            raise ValueError("native no-rollback turn must be an object")
        if payload.get("schema") == "agenttx.active_turn_history/v1":
            turn_index = turn.get("turn_index")
            operation_count = turn.get("operation_count")
            result = turn.get("tool_result")
            if (
                not isinstance(turn_index, int)
                or not isinstance(operation_count, int)
                or not isinstance(result, dict)
                or int(result.get("exit_code", 1)) != 0
            ):
                raise ValueError("native turn receipt is malformed")
            content = str(turn.get("assistant_message") or "")
            messages.append(
                {
                    "role": "assistant",
                    "content": content,
                    "thought": content,
                    "agent": agent_name,
                    "message_type": "thought",
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "<observation>"
                        + str(result.get("stdout") or "")
                        + "</observation>"
                    ),
                    "agent": agent_name,
                    "message_type": "observation",
                }
            )
            continue
        call = turn.get("assistant_tool_call")
        result = turn.get("tool_result")
        if not isinstance(call, dict) or not isinstance(result, dict):
            raise ValueError("native no-rollback turn must contain a call and result")
        arguments = call.get("arguments")
        command = arguments.get("cmd") if isinstance(arguments, dict) else None
        if not isinstance(command, list) or not command:
            raise ValueError("native no-rollback call must contain command argv")
        rendered_command = shlex.join(str(argument) for argument in command)
        if int(result.get("exit_code", 1)) != 0:
            raise ValueError("native no-rollback history accepts only successful calls")
        assistant_content = (
            "THOUGHT: Continue the original workload with the next indexed operation.\n\n"
            f"```bash\n{rendered_command}\n```"
        )
        messages.append(
            {
                "role": "assistant",
                "content": assistant_content,
                "thought": "Continue the original workload with the next indexed operation.",
                "action": rendered_command,
                "agent": agent_name,
                "message_type": "action",
            }
        )
        messages.append(
            {
                "role": "user",
                "content": (
                    "<warning>Your last command ran successfully and did not produce "
                    "any output.</warning>"
                ),
                "agent": agent_name,
                "message_type": "observation",
            }
        )
    return messages


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
        max_steps: int = 192,
        max_model_calls: int = 192,
        timeout_s: Optional[float] = None,
    ) -> None:
        self.output_dir = Path(output_dir)
        self.profile = profile
        self.model = profile.model
        self.max_steps = max_steps
        self.timeout_s = timeout_s
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
        return self._run(task, workdir, env=env, commit=True)

    def run_in_transaction(self, transaction_harness, task: str) -> SWEAgentHarnessResult:
        """Run native SWEAgent inside an official runner's existing transaction."""
        return self._run(
            task,
            Path(transaction_harness.workdir),
            env=None,
            transaction_harness=transaction_harness,
            commit=False,
        )

    def _run(
        self,
        task: str,
        workdir: Path,
        *,
        env: Optional[Dict[str, str]],
        transaction_harness=None,
        commit: bool,
    ) -> SWEAgentHarnessResult:
        if os.environ.get("AGENTTX_MOTIVATION_BWRAP") != "1":
            raise RuntimeError(
                "SWEAgent-native M3 must run in the dedicated bwrap namespace"
            )
        workdir = Path(workdir).resolve()
        run_env = env or {}
        cell_id = run_env.get("AGENTTX_CELL_ID", workdir.parent.name)
        diagnosis_required = run_env.get("AGENTTX_DIAGNOSIS_REQUIRED") == "1"
        native_active_history = run_env.get("AGENTTX_NATIVE_ACTIVE_HISTORY", "")
        native_base_task = run_env.get("AGENTTX_NATIVE_BASE_TASK", "")
        native_crash_notice = run_env.get("AGENTTX_NATIVE_CRASH_NOTICE", "")
        native_resume = bool(native_active_history or native_base_task or native_crash_notice)
        if native_resume and not all(
            (native_active_history, native_base_task, native_crash_notice)
        ):
            raise ValueError("native no-rollback resume context is incomplete")
        cell_output = (
            self.output_dir
            / "trajectories"
            / cell_id
            / f"attempt-{uuid.uuid4().hex}"
        )
        session_dir = (
            Path(transaction_harness.tx.pool.session_dir)
            if transaction_harness is not None
            else workdir.parent / "session"
        )
        agent = None
        adapter = None
        swe_env = None
        started = time.perf_counter()
        deadline = (
            started + self.timeout_s
            if self.timeout_s is not None and self.timeout_s > 0
            else None
        )

        def enforce_deadline() -> None:
            # SWEAgent may catch a SIGALRM-raised exception inside its model
            # loop. Recheck elapsed time at the outer step boundary so a
            # swallowed signal cannot silently extend the experiment cell.
            if deadline is not None and time.perf_counter() >= deadline:
                raise _SWEAgentWallTimeout(
                    "SWEAgent workload exceeded its wall-clock timeout"
                )

        old_alarm_handler = None
        if self.timeout_s is not None and self.timeout_s > 0:
            old_alarm_handler = signal.signal(signal.SIGALRM, _raise_wall_timeout)
            signal.setitimer(signal.ITIMER_REAL, self.timeout_s)
        _cleanup_sweagent_runtime()
        try:
            _initialize_workspace_repo(workdir)
            base_prompt = native_base_task or task
            clean_baseline = base_prompt.startswith(
                "This is the clean compact recovery baseline."
            )
            prompt = base_prompt + _workload_suffix(
                diagnosis_required,
                clean_baseline=clean_baseline,
            )
            problem = TextProblemStatement(text=prompt, id=cell_id)
            agent_config = self.config.model_copy(deep=True)
            if clean_baseline:
                agent_config.templates.system_template = (
                    "Return exactly one bash code block containing only: submit"
                )
                agent_config.templates.instance_template = (
                    "{{problem_statement}}\n\nThe verified clean baseline requires "
                    "exactly one action: `submit`. Do not inspect files or run any "
                    "other command."
                )
            agent = get_agent_from_config(agent_config)
            swe_env = SWEEnv(
                deployment=get_deployment(LocalDeploymentConfig()),
                repo=None,
                post_startup_commands=[],
            )
            swe_env.start()
            swe_env.communicate(f"cd -- {workdir}", check="raise")
            swe_env.set_env_variables({"ROOT": str(workdir)})
            if transaction_harness is None:
                adapter = create_swe_agent_adapter(
                    agent,
                    workdir=workdir,
                    session_dir=session_dir,
                    trace_backend="strace",
                    # SWEAgent's submit bundle writes model.patch under the bwrap
                    # tmpfs /root; it is tooling output, not part of the workload.
                    ephemeral_roots=(Path("/root"),),
                )
            else:
                adapter = SWEAgentNativeAdapter(
                    agent=agent,
                    harness=transaction_harness,
                )
            if native_resume:
                adapter.setup(swe_env, problem, cell_output)
                native_messages = _native_active_history_messages(
                    native_active_history,
                    agent.name,
                )
                for message in native_messages:
                    agent._append_history(message)
                agent._append_history(
                    {
                        "role": "user",
                        "content": native_crash_notice,
                        "agent": agent.name,
                        "message_type": "observation",
                    }
                )
                completed = 0
                step_output = None
                while step_output is None or not bool(
                    getattr(step_output, "done", False)
                ):
                    enforce_deadline()
                    if completed >= self.max_steps:
                        raise SWEAgentIntegrationError(
                            f"SWE-agent exceeded AgentTX max_steps={self.max_steps}"
                        )
                    step_output = adapter.step()
                    enforce_deadline()
                    completed += 1
                    agent.save_trajectory()
                result = adapter.finish(commit=commit)
            else:
                native_messages = []
                result = adapter.run(
                    swe_env,
                    problem,
                    cell_output,
                    max_steps=1 if clean_baseline else self.max_steps,
                    commit=commit,
                )
            submitted = str(result.summary).startswith("submitted")
            events = []
            for index, step in enumerate(getattr(agent, "trajectory", []) or []):
                if not isinstance(step, dict):
                    continue
                action = step.get("action")
                if isinstance(action, dict):
                    command = str(
                        action.get("command")
                        or action.get("action")
                        or action.get("code")
                        or ""
                    )
                else:
                    command = str(action or "")
                if not command:
                    continue
                events.append(
                    {
                        "type": "item.completed",
                        "item": {
                            "id": f"sweagent-step-{index}",
                            "type": "command_execution",
                            "command": command,
                        },
                    }
                )
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
                events=events,
                metadata={
                    "harness_backend": self.name,
                    "sweagent_model": self.model,
                    "sweagent_config_hash": self.config_hash,
                    "sweagent_exit_status": result.summary,
                    "sweagent_model_calls": result.model_calls,
                    "native_adapter": "SWEAgentNativeAdapter",
                    "native_active_history": native_resume,
                    "native_active_history_turns": len(native_messages) // 2,
                    "trajectory_dir": str(cell_output),
                },
            )
        except Exception as exc:
            stats = getattr(getattr(agent, "model", None), "stats", None)
            prompt_tokens = int(getattr(stats, "tokens_sent", 0) or 0)
            completion_tokens = int(getattr(stats, "tokens_received", 0) or 0)
            model_calls = int(getattr(stats, "api_calls", 0) or 0)
            timed_out = isinstance(exc, _SWEAgentWallTimeout)
            return SWEAgentHarnessResult(
                harness=self.name,
                model=self.model,
                returncode=124 if timed_out else 127,
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
                    "native_active_history": native_resume,
                    "native_active_history_turns": (
                        len(native_messages) // 2 if "native_messages" in locals() else 0
                    ),
                    "trajectory_dir": str(cell_output),
                },
            )
        finally:
            if old_alarm_handler is not None:
                signal.setitimer(signal.ITIMER_REAL, 0)
                signal.signal(signal.SIGALRM, old_alarm_handler)
            if adapter is not None and transaction_harness is None:
                with suppress(Exception):
                    adapter.close(destroy=True)
            if swe_env is not None:
                deployment = swe_env.deployment
                with suppress(Exception):
                    swe_env.close()
                # SWEEnv.close() synchronously stops the deployment. Break its
                # final strong reference now so SWE-ReX's defensive __del__
                # runs while asyncio is still fully initialized, rather than
                # during interpreter teardown.
                with suppress(Exception):
                    swe_env.deployment = None
                del deployment
            with suppress(Exception):
                _cleanup_sweagent_runtime()
