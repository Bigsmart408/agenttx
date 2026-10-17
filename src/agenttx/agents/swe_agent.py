"""In-process SWE-agent integration with AgentTX context reconciliation.

This module intentionally does not use the external-agent subprocess adapter.
It keeps a live SWE-agent ``DefaultAgent`` instance in the caller's process,
routes every action through AgentTX, and projects the agent's mutable history
after a rollback.  SWE-agent is an optional dependency and is imported only
when callers construct and pass the SWE-agent object.

The environment and AgentTX must share a filesystem namespace.  In particular,
passing a Docker-backed SWEEnv to an AgentTX instance running on the host is not
supported: the paths visible to the two runtimes would describe different
objects and any dependency graph would be unsound.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import shlex
import uuid
from dataclasses import dataclass, field
from pathlib import Path, PurePath
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence

from agenttx.harness import CodingAgentHarness
from agenttx.ledger import Effect, EffectKind
from agenttx.runtime import ToolCallRecord


STEP_IDS_KEY = "agenttx_step_ids"
RECOVERY_GENERATION_KEY = "agenttx_recovery_generation"
PRECRASH_SEEDED_KEY = "agenttx_precrash_seeded"
_PROJECTOR_MARKER = "_agenttx_swe_history_projector"


class SWEAgentIntegrationError(RuntimeError):
    """Raised when SWE-agent cannot be integrated without losing semantics."""


def _as_step_ids(value: object) -> List[int]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)):
        return []
    if isinstance(value, int):
        return [value]
    try:
        return [int(item) for item in value]  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return []


def _recovery_notice(conversation: Any) -> str:
    for turn in reversed(conversation.turns):
        if turn.status != "active" or turn.kind != "recovery":
            continue
        for message in turn.messages:
            if message.get("role") == "user":
                return str(message.get("content") or "")
    return ""


class SWEAgentHistoryProjector:
    """SWE-agent history processor backed by AgentTX's conversation graph.

    The canonical SWE-agent history remains an append-only audit record.  This
    processor changes the live model view: a complete action/observation pair
    is omitted when any AgentTX step associated with that SWE-agent step was
    invalidated.  The latest recovery notice is synthesized into the view so a
    rollback performed directly on the transaction is still visible to the
    next model query.
    """

    _agenttx_swe_history_projector = True

    def __init__(self, adapter: "SWEAgentNativeAdapter") -> None:
        self.adapter = adapter

    def __call__(self, history: Sequence[Mapping[str, Any]]) -> List[dict]:
        active_ids = set(self.adapter.harness.tx.conversation.active_step_ids())
        known_ids = {
            step_id
            for item in history
            for step_id in _as_step_ids(item.get(STEP_IDS_KEY))
        }
        invalidated_ids = known_ids - active_ids
        generation = self.adapter.harness.tx.conversation.generation

        projected: List[dict] = []
        has_current_notice = False
        for source in history:
            step_ids = set(_as_step_ids(source.get(STEP_IDS_KEY)))
            if step_ids & invalidated_ids:
                continue
            notice_generation = source.get(RECOVERY_GENERATION_KEY)
            if notice_generation is not None:
                try:
                    notice_generation = int(notice_generation)
                except (TypeError, ValueError):
                    continue
                if notice_generation != generation:
                    continue
                has_current_notice = True
            projected.append(dict(source))

        if self.adapter.recovery_context_variant == "compact":
            seeded = [
                index for index, item in enumerate(projected)
                if item.get(PRECRASH_SEEDED_KEY)
            ]
            if len(seeded) > 12:
                keep = set(seeded[-12:])
                projected = [
                    item for index, item in enumerate(projected)
                    if not item.get(PRECRASH_SEEDED_KEY) or index in keep
                ]
                first = next(
                    index for index, item in enumerate(projected)
                    if item.get(PRECRASH_SEEDED_KEY)
                )
                projected.insert(first, {
                    "role": "user",
                    "content": (
                        "Earlier retained tool observations are omitted from this "
                        "compact view; the recovery handoff describes current state."
                    ),
                    "agent": self.adapter.agent.name,
                    "message_type": "observation",
                })

        notice = _recovery_notice(self.adapter.harness.tx.conversation)
        if generation > 0 and notice and not has_current_notice:
            projected.append(
                {
                    "role": "user",
                    "content": notice,
                    "agent": self.adapter.agent.name,
                    "message_type": "observation",
                    RECOVERY_GENERATION_KEY: generation,
                }
            )
        return projected


class AgentTXSWEEnvironment:
    """SWEEnv-compatible proxy that executes live actions through AgentTX.

    Setup calls are delegated to the original SWEEnv.  After ``activate()``,
    shell communication and direct file operations use the AgentTX overlay.
    This preserves SWE-agent's own model loop while replacing its execution
    path with per-action AgentTX transactions. Persistent working-directory
    changes are modeled explicitly; arbitrary shell state such as aliases and
    action-spanning ``export`` commands is intentionally not emulated.
    """

    def __init__(self, environment: Any, harness: CodingAgentHarness) -> None:
        self._environment = environment
        self._harness = harness
        self._active = False
        self._cwd = harness.workdir
        self._initial_cwd = harness.workdir
        self._cwd_writer_step_id: Optional[int] = None
        self._cwd_versions: Dict[int, Path] = {}
        self._environment_variables: Dict[str, str] = {}
        self._pending: List[tuple[ToolCallRecord, str]] = []
        self._marker = f"__AGENTTX_CWD_{uuid.uuid4().hex}__"

    def __getattr__(self, name: str) -> Any:
        return getattr(self._environment, name)

    @property
    def active(self) -> bool:
        return self._active

    def activate(self) -> None:
        """Switch from SWEEnv setup to AgentTX-backed command execution."""
        cwd_text = self._environment.communicate("pwd", check="raise").strip()
        cwd = Path(cwd_text).resolve()
        workspace = self._harness.workdir.resolve()
        try:
            cwd.relative_to(workspace)
        except ValueError as exc:
            raise SWEAgentIntegrationError(
                "SWE-agent and AgentTX do not share the same workspace namespace: "
                f"SWEEnv cwd is {cwd}, AgentTX workspace is {workspace}. "
                "Use an in-process/local SWEEnv inside the AgentTX namespace."
            ) from exc

        for path in (Path("/"), cwd):
            host_stat = path.stat()
            identity = self._environment.communicate(
                f"stat -Lc '%d:%i' -- {shlex.quote(str(path))}", check="raise"
            ).strip()
            expected_identity = f"{host_stat.st_dev}:{host_stat.st_ino}"
            if identity != expected_identity:
                raise SWEAgentIntegrationError(
                    "SWE-agent and AgentTX resolved a namespace path to different "
                    f"objects ({path}: SWEEnv={identity}, "
                    f"AgentTX={expected_identity}). A Docker/remote SWEEnv cannot "
                    "be paired with host-side AgentTX."
                )

        self._cwd = cwd
        self._initial_cwd = cwd
        encoded_environment = self._environment.communicate(
            "env -0 | base64 -w0", check="raise"
        ).strip()
        try:
            environment_blob = base64.b64decode(encoded_environment, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise SWEAgentIntegrationError(
                "could not capture SWE-agent's initialized shell environment"
            ) from exc
        for entry in environment_blob.split(b"\0"):
            name, separator, value = entry.partition(b"=")
            if not separator:
                continue
            decoded_name = name.decode("utf-8", errors="surrogateescape")
            if decoded_name in {"PWD", "OLDPWD", "SHLVL", "_"}:
                continue
            self._environment_variables[decoded_name] = value.decode(
                "utf-8", errors="surrogateescape"
            )
        self._active = True

    def deactivate(self) -> None:
        self._active = False

    def begin_step(self) -> None:
        if self._pending:
            raise SWEAgentIntegrationError(
                "unbound SWE-agent commands remain from the previous step"
            )

    def take_step_records(self) -> List[tuple[ToolCallRecord, str]]:
        records = list(self._pending)
        self._pending.clear()
        return records

    def _link_cwd_dependency(self, record: ToolCallRecord) -> None:
        parent = self._cwd_writer_step_id
        if parent is None or parent == record.step_id:
            return
        ledger_step = self._harness.tx.ledger.steps[record.step_id]
        ledger_step.parents.add(parent)
        record.parents = sorted(set(record.parents) | {parent})
        self._harness.tx._persist()

    def reconcile_after_rollback(self) -> None:
        """Restore persistent-shell cwd metadata to the retained graph head."""
        active = {
            step.step_id
            for step in self._harness.tx.ledger.steps
            if step.status != "rolled_back"
        }
        writers = sorted(set(self._cwd_versions) & active)
        if not writers:
            self._cwd = self._initial_cwd
            self._cwd_writer_step_id = None
            return
        self._cwd_writer_step_id = writers[-1]
        self._cwd = self._cwd_versions[writers[-1]]

    def _shell_script(
        self,
        command: str,
        *,
        cwd: Optional[Path] = None,
        environment: Optional[Mapping[str, str]] = None,
        track_cwd: bool = True,
    ) -> str:
        selected_cwd = Path(cwd or self._cwd)
        variables = dict(self._environment_variables)
        variables.update({str(key): str(value) for key, value in (environment or {}).items()})
        exports = "\n".join(
            f"export {name}={shlex.quote(value)}"
            for name, value in variables.items()
            if name and name.replace("_", "a").isalnum() and not name[0].isdigit()
        )
        suffix = ""
        if track_cwd:
            suffix = (
                "\n_agenttx_status=$?\n"
                f"printf '\\n{self._marker}%s\\n' \"$PWD\"\n"
                "exit \"$_agenttx_status\"\n"
            )
        return (
            "set +e\n"
            f"cd -- {shlex.quote(str(selected_cwd))} || exit $?\n"
            f"{exports}\n"
            f"{command}"
            f"{suffix}"
        )

    def _record_command(
        self,
        command: str,
        *,
        timeout: Optional[float],
        tool_name: str,
        cwd: Optional[Path] = None,
        environment: Optional[Mapping[str, str]] = None,
        track_cwd: bool = True,
    ) -> ToolCallRecord:
        previous_cwd = self._cwd
        script = self._shell_script(
            command,
            cwd=cwd,
            environment=environment,
            track_cwd=track_cwd,
        )
        record = self._harness.tx.run_tool(
            tool_name,
            ["bash", "-c", script],
            timeout_s=timeout,
        )
        self._link_cwd_dependency(record)
        if track_cwd:
            marker = "\n" + self._marker
            marker_index = record.stdout.rfind(marker)
            if marker_index >= 0:
                marker_tail = record.stdout[marker_index + len(marker) :]
                cwd_value = marker_tail.split("\n", 1)[0]
                if cwd_value:
                    self._cwd = Path(cwd_value)
                record.stdout = record.stdout[:marker_index]
            if self._cwd != previous_cwd:
                self._cwd_writer_step_id = record.step_id
                self._cwd_versions[record.step_id] = self._cwd
        self._pending.append((record, command))
        return record

    @staticmethod
    def _output(record: ToolCallRecord) -> str:
        pieces = [piece for piece in (record.stdout, record.stderr) if piece]
        return "\n".join(piece.rstrip("\n") for piece in pieces)

    def communicate(
        self,
        input: str,
        timeout: int | float = 25,
        *,
        check: Literal["warn", "ignore", "raise"] = "ignore",
        error_msg: str = "Command failed",
    ) -> str:
        if not self._active:
            return self._environment.communicate(
                input=input,
                timeout=timeout,
                check=check,
                error_msg=error_msg,
            )
        record = self._record_command(
            input,
            timeout=float(timeout),
            tool_name="sweagent:command",
        )
        output = self._output(record)
        if record.returncode == 124:
            message = (
                f"timeout after {float(timeout):.1f} seconds while running "
                f"command {input!r}"
            )
            try:
                from swerex.exceptions import CommandTimeoutError
            except ImportError as exc:  # pragma: no cover - broken SWE-agent install
                raise SWEAgentIntegrationError(message) from exc
            raise CommandTimeoutError(message)
        if check == "raise" and record.returncode != 0:
            raise RuntimeError(
                f"Command {input!r} failed (exit_code={record.returncode}): {error_msg}\n{output}"
            )
        return output

    def read_file(
        self,
        path: str | PurePath,
        encoding: Optional[str] = None,
        errors: Optional[str] = None,
    ) -> str:
        if not self._active:
            return self._environment.read_file(path, encoding=encoding, errors=errors)
        logical = Path(path)
        relative = not logical.is_absolute()
        if relative:
            logical = self._cwd / logical
        logical = Path(os.path.abspath(logical))
        effect_kind = (
            EffectKind.READ
            if self._harness.tx.path_exists(logical)
            else EffectKind.NEGATIVE
        )
        record = self._harness.tx.run_tool(
            "sweagent:read_file",
            ["bash", "-c", f"cat -- {shlex.quote(str(logical))}"],
            extra_effects=[Effect(str(logical), effect_kind)],
            trace_reads=False,
        )
        if relative:
            self._link_cwd_dependency(record)
        arguments = json.dumps(
            {"op": "read_file", "path": str(path)},
            sort_keys=True,
        )
        self._pending.append((record, arguments))
        if record.returncode != 0:
            raise FileNotFoundError(str(path))
        return record.stdout

    def write_file(self, path: str | PurePath, content: str) -> None:
        if not self._active:
            self._environment.write_file(path, content)
            return
        logical = Path(path)
        relative = not logical.is_absolute()
        if relative:
            logical = self._cwd / logical
        encoded = base64.b64encode(content.encode("utf-8")).decode("ascii")
        command = (
            f"mkdir -p -- {shlex.quote(str(logical.parent))} && "
            f"printf %s {shlex.quote(encoded)} | base64 -d > "
            f"{shlex.quote(str(logical))}"
        )
        record = self._harness.tx.run_tool(
            "sweagent:write_file",
            ["bash", "-c", command],
            trace_reads=False,
        )
        if relative:
            self._link_cwd_dependency(record)
        arguments = json.dumps(
            {"op": "write_file", "path": str(path), "content": content},
            sort_keys=True,
        )
        self._pending.append((record, arguments))
        if record.returncode != 0:
            raise RuntimeError(f"could not write {path}: {record.stderr}")

    def set_env_variables(self, env_variables: Mapping[str, object]) -> None:
        values = {str(key): str(value) for key, value in env_variables.items()}
        if self._active:
            raise SWEAgentIntegrationError(
                "changing persistent SWE-agent environment variables during an "
                "AgentTX-protected run is unsupported; pass action-local variables "
                "through execute_command(env=...)"
            )
        self._environment_variables.update(values)
        self._environment.set_env_variables(values)

    def execute_command(
        self,
        command: str,
        shell: bool = True,
        check: bool = False,
        env: Optional[Mapping[str, str]] = None,
        cwd: Optional[str] = None,
    ) -> None:
        if not self._active:
            self._environment.execute_command(
                command, shell=shell, check=check, env=env, cwd=cwd
            )
            return
        if not shell:
            raise SWEAgentIntegrationError(
                "AgentTX's SWE-agent adapter requires shell=True for execute_command"
            )
        record = self._record_command(
            command,
            timeout=None,
            tool_name="sweagent:execute",
            cwd=Path(cwd) if cwd else self._cwd,
            environment=env,
            track_cwd=False,
        )
        if check and record.returncode != 0:
            raise RuntimeError(
                f"Command {command!r} failed with exit code {record.returncode}"
            )


@dataclass
class SWEAgentNativeRunResult:
    """Stable AgentTX wrapper result independent of SWE-agent's result schema."""

    info: dict
    trajectory: list
    messages: List[dict]
    ledger: dict
    context_generation: int
    tool_calls: int = 0
    model_calls: int = 0
    finished: bool = False
    summary: str = ""
    committed: bool = False
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    usage_source: str = "sweagent_model_stats"
    rollback_events: List[dict] = field(default_factory=list)


class SWEAgentNativeAdapter:
    """Run a live SWE-agent instance as an AgentTX-integrated native agent."""

    def __init__(self, agent: Any, harness: CodingAgentHarness) -> None:
        self.agent = agent
        self.harness = harness
        self.environment: Optional[AgentTXSWEEnvironment] = None
        self.rollback_events: List[dict] = []
        self._run_started = False
        self._run_finished = False
        self._committed = False
        self.recovery_context_variant = "full"
        self._install_history_projector()

    def _install_history_projector(self) -> None:
        processors = getattr(self.agent, "history_processors", None)
        if processors is None:
            raise SWEAgentIntegrationError(
                "SWE-agent compatibility error: agent has no history_processors"
            )
        if any(getattr(item, _PROJECTOR_MARKER, False) for item in processors):
            raise SWEAgentIntegrationError(
                "this SWE-agent instance is already attached to AgentTX"
            )
        processors.insert(0, SWEAgentHistoryProjector(self))

    def _seed_conversation(self) -> None:
        conversation = self.harness.tx.conversation
        if conversation.has_seed():
            return
        system = ""
        task = ""
        for item in self.agent.history:
            role = item.get("role")
            content = item.get("content")
            if not isinstance(content, str):
                continue
            if role == "system" and not system:
                system = content
            elif role == "user" and not task and not item.get("is_demo"):
                task = content
        conversation.seed(system, task)
        self.harness.tx._persist()

    @staticmethod
    def _tool_result(record: ToolCallRecord) -> str:
        return json.dumps(
            {
                "step_id": record.step_id,
                "exit_code": record.returncode,
                "stdout": (record.stdout or "")[-4000:],
                "stderr": (record.stderr or "")[-2000:],
                "parents": list(record.parents),
            },
            sort_keys=True,
        )

    def _record_swe_step(
        self,
        step: Any,
        records: Sequence[tuple[ToolCallRecord, str]],
    ) -> List[int]:
        if not records:
            return []
        calls = []
        messages = []
        step_ids = []
        tool_names = []
        for record, command in records:
            call_id = f"sweagent-{record.step_id}"
            calls.append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": record.tool_name,
                        "arguments": json.dumps({"cmd": command}, sort_keys=True),
                    },
                }
            )
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": self._tool_result(record),
                }
            )
            step_ids.append(record.step_id)
            tool_names.append(record.tool_name)
        assistant = {
            "role": "assistant",
            "content": str(getattr(step, "output", "") or ""),
            "tool_calls": calls,
        }
        self.harness.tx.conversation.append_turn(
            [assistant, *messages],
            step_ids=step_ids,
            tool_names=tool_names,
            kind="effect",
        )
        self.harness.tx._persist()
        return step_ids

    def _tag_history(self, first: int, step_ids: Sequence[int]) -> None:
        if not step_ids:
            return
        for item in self.agent.history[first:]:
            item[STEP_IDS_KEY] = list(step_ids)

    def step(self) -> Any:
        """Execute one SWE-agent model/action turn through AgentTX."""
        if self.environment is None or not self.environment.active:
            raise SWEAgentIntegrationError("SWE-agent adapter has not been set up")
        if self._run_finished:
            raise SWEAgentIntegrationError("this SWE-agent run is already finished")
        history_start = len(self.agent.history)
        self.environment.begin_step()
        try:
            step = self.agent.step()
        except BaseException:
            records = self.environment.take_step_records()
            if records:
                self._record_swe_step(None, records)
            raise
        records = self.environment.take_step_records()
        step_ids = self._record_swe_step(step, records)
        self._tag_history(history_start, step_ids)
        return step

    def _append_recovery_notice(self) -> None:
        generation = self.harness.tx.conversation.generation
        notice = _recovery_notice(self.harness.tx.conversation)
        if not notice:
            return
        self.agent._append_history(
            {
                "role": "user",
                "content": notice,
                "agent": self.agent.name,
                "message_type": "observation",
            }
        )
        self.agent.history[-1][RECOVERY_GENERATION_KEY] = generation

    def rollback_causal(self, step_id: int) -> List[int]:
        targets = self.harness.tx.rollback_causal(step_id)
        if self.environment is not None:
            self.environment.reconcile_after_rollback()
        if targets:
            self._append_recovery_notice()
        self.rollback_events.append(
            {
                "mode": "causal",
                "root_step_id": int(step_id),
                "targets": list(targets),
                "generation": self.harness.tx.conversation.generation,
            }
        )
        return targets

    def rollback_temporal(self, step_id: int) -> List[int]:
        targets = self.harness.tx.rollback(step_id)
        if self.environment is not None:
            self.environment.reconcile_after_rollback()
        if targets:
            self._append_recovery_notice()
        self.rollback_events.append(
            {
                "mode": "temporal",
                "root_step_id": int(step_id),
                "targets": list(targets),
                "generation": self.harness.tx.conversation.generation,
            }
        )
        return targets

    def setup(
        self,
        environment: Any,
        problem_statement: Any,
        output_dir: Path = Path("."),
    ) -> None:
        """Set up one live run and activate transactional execution."""
        if self._run_started:
            raise SWEAgentIntegrationError("this SWE-agent run is already set up")
        required = (
            "setup",
            "step",
            "save_trajectory",
            "get_trajectory_data",
            "history",
            "history_processors",
            "trajectory",
            "info",
            "name",
            "_append_history",
        )
        missing = [name for name in required if not hasattr(self.agent, name)]
        if missing:
            raise SWEAgentIntegrationError(
                "unsupported SWE-agent version; missing: " + ", ".join(missing)
            )

        proxy = AgentTXSWEEnvironment(environment, self.harness)
        self.environment = proxy
        self.agent.setup(
            env=proxy,
            problem_statement=problem_statement,
            output_dir=Path(output_dir),
        )
        # SWE-agent setup may reset or populate the repository before the first
        # protected action. Refresh object identities against that final base.
        self.harness.tx.object_catalog.refresh(self.harness.workdir)
        self.harness.tx._catalog_initialized = True
        self._seed_conversation()
        proxy.activate()

        hooks = getattr(self.agent, "_chook", None)
        if hooks is not None:
            hooks.on_run_start()
        self._run_started = True

    def commit(self) -> int:
        """Materialize every retained SWE-agent effect into the workspace."""
        frontier = self.harness.tx.commit()
        self._committed = True
        return frontier

    def finish(self, *, commit: bool = False) -> SWEAgentNativeRunResult:
        """Finish SWE-agent bookkeeping and return the integrated result."""
        if not self._run_started:
            raise SWEAgentIntegrationError("SWE-agent adapter has not been set up")
        if not self._run_finished:
            self.agent.save_trajectory()
            hooks = getattr(self.agent, "_chook", None)
            if hooks is not None:
                hooks.on_run_done(
                    trajectory=self.agent.trajectory,
                    info=self.agent.info,
                )
            self._run_finished = True

        data = self.agent.get_trajectory_data()
        if commit and not self._committed:
            self.commit()
        info = dict(data.get("info") or {})
        trajectory = list(data.get("trajectory") or [])
        model_stats = info.get("model_stats") or {}
        if not isinstance(model_stats, Mapping):
            model_stats = {}
        prompt_tokens = int(model_stats.get("tokens_sent") or 0)
        completion_tokens = int(model_stats.get("tokens_received") or 0)
        return SWEAgentNativeRunResult(
            info=info,
            trajectory=trajectory,
            messages=[dict(item) for item in self.agent.messages],
            ledger=self.harness.tx.ledger.to_dict(),
            context_generation=self.harness.tx.conversation.generation,
            tool_calls=len(trajectory),
            model_calls=int(model_stats.get("api_calls") or 0),
            finished=self._run_finished,
            summary=str(info.get("exit_status") or ""),
            committed=self._committed,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
            rollback_events=list(self.rollback_events),
        )

    def run(
        self,
        environment: Any,
        problem_statement: Any,
        output_dir: Path = Path("."),
        *,
        max_steps: Optional[int] = None,
        commit: bool = False,
    ) -> SWEAgentNativeRunResult:
        """Run SWE-agent's live step loop without the black-box CLI adapter."""
        self.setup(environment, problem_statement, output_dir)
        completed = 0
        step_output = None
        while step_output is None or not bool(getattr(step_output, "done", False)):
            if max_steps is not None and completed >= max_steps:
                raise SWEAgentIntegrationError(
                    f"SWE-agent exceeded AgentTX max_steps={max_steps}"
                )
            step_output = self.step()
            completed += 1
            self.agent.save_trajectory()
        return self.finish(commit=commit)

    def close(self, destroy: bool = True) -> None:
        if self.environment is not None:
            self.environment.deactivate()
        self.harness.close(destroy=destroy)


def create_swe_agent_adapter(
    agent: Any,
    *,
    workdir: Path,
    session_dir: Optional[Path] = None,
    trace_backend: str = "auto",
    ephemeral_roots: Iterable[Path] = (),
) -> SWEAgentNativeAdapter:
    """Construct the in-process SWE-agent adapter with an AgentTX harness."""
    harness = CodingAgentHarness(
        workdir=Path(workdir),
        session_dir=session_dir,
        trace_backend=trace_backend,
        ephemeral_roots=tuple(ephemeral_roots),
    )
    try:
        return SWEAgentNativeAdapter(agent=agent, harness=harness)
    except BaseException:
        harness.close(destroy=True)
        raise
