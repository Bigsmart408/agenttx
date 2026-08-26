"""Adapters for the external agent harnesses used by application benchmarks.

The application evaluation must exercise the same agent stacks as the systems
we compare against.  This module deliberately does not implement an LLM loop.
It starts the DeepSeek Harness headless profile or the official Codex CLI and
lets that harness own prompts, tools, retries, and model configuration.  The
AgentTX transaction bridge only supplies the protected workspace and records
the external process as one task execution boundary until a turn-level hook is
available.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from ..providers import load_provider_env

try:  # Optional at import time; the remote benchmark environment installs it.
    import zstandard as _zstandard
except ImportError:  # pragma: no cover - exercised only on minimal installations
    _zstandard = None


@dataclass(frozen=True)
class ExternalHarnessResult:
    """Normalized result returned by a real external harness."""

    harness: str
    model: str
    returncode: int
    duration_s: float
    stdout: str = ""
    stderr: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    tool_calls: int = 0
    usage_source: str = "none"
    events: List[dict] = field(default_factory=list)

    @property
    def finished(self) -> bool:
        return self.returncode == 0


def _token_usage(value: Any) -> tuple[int, int, int]:
    """Extract common usage fields from a JSON event without assuming a schema."""
    if isinstance(value, dict):
        usage = value.get("usage")
        if isinstance(usage, dict):
            # DeepSeek Harness reports the uncached prompt and cache traffic
            # separately (inputTokens/cacheReadTokens/cacheWriteTokens),
            # whereas OpenAI-compatible responses fold cached input into
            # prompt_tokens.  Normalize both to prompt pressure plus output;
            # this keeps DS cache reads from silently becoming zero-token runs.
            dsh_shape = any(
                key in usage
                for key in ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")
            )
            if dsh_shape:
                uncached = int(usage.get("inputTokens", usage.get("input_tokens", 0)) or 0)
                cache_read = int(
                    usage.get("cacheReadTokens", usage.get("cache_read_input_tokens", 0)) or 0
                )
                cache_write = int(
                    usage.get("cacheWriteTokens", usage.get("cache_creation_input_tokens", 0)) or 0
                )
                prompt = uncached + cache_read + cache_write
                completion = int(usage.get("outputTokens", usage.get("output_tokens", 0)) or 0)
            else:
                prompt = int(
                    usage.get(
                        "prompt_tokens",
                        usage.get("input_tokens", usage.get("inputTokens", 0)),
                    )
                    or 0
                )
                completion = int(
                    usage.get(
                        "completion_tokens",
                        usage.get("output_tokens", usage.get("outputTokens", 0)),
                    )
                    or 0
                )
            total = int(
                usage.get(
                    "total_tokens", usage.get("totalTokens", prompt + completion)
                )
                or 0
            )
            # Some providers report a total that excludes cache buckets.  For
            # DSH the normalized prompt pressure is the authoritative total.
            if dsh_shape and total < prompt + completion:
                total = prompt + completion
            return prompt, completion, total
        totals = [0, 0, 0]
        for child in value.values():
            found = _token_usage(child)
            totals = [left + right for left, right in zip(totals, found)]
        return tuple(totals)  # type: ignore[return-value]
    if isinstance(value, list):
        totals = [0, 0, 0]
        for child in value:
            found = _token_usage(child)
            totals = [left + right for left, right in zip(totals, found)]
        return tuple(totals)  # type: ignore[return-value]
    return 0, 0, 0


def _parse_jsonl(stdout: str) -> tuple[List[dict], tuple[int, int, int], int]:
    events: List[dict] = []
    usage = [0, 0, 0]
    tool_calls = 0
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        events.append(event)
        found = _token_usage(event)
        usage = [left + right for left, right in zip(usage, found)]
        event_type = str(event.get("type", event.get("event", ""))).lower()
        if "tool" in event_type and any(
            key in event for key in ("name", "tool", "tool_name", "function")
        ):
            tool_calls += 1
    return events, tuple(usage), tool_calls  # type: ignore[return-value]


def _read_session_jsonl(path: Path) -> List[dict]:
    """Read one DeepSeek Harness JSONL session, including concatenated zstd."""
    try:
        if path.name.endswith(".zstd"):
            text = ""
            if _zstandard is not None:
                try:
                    with path.open("rb") as handle:
                        raw = _zstandard.ZstdDecompressor().stream_reader(handle).read()
                    text = raw.decode("utf-8", "replace")
                except (OSError, ValueError):
                    text = ""
            # DSH appends independent zstd frames.  Keep a CLI fallback for
            # minimal benchmark environments and for decoders that stop at the
            # first frame instead of consuming the complete stream.
            if not text:
                decoder = shutil.which("unzstd") or shutil.which("zstd")
                if not decoder:
                    return []
                decoded = subprocess.run(
                    [decoder, "-c", str(path)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    check=False,
                )
                if decoded.returncode != 0:
                    return []
                text = decoded.stdout.decode("utf-8", "replace")
        else:
            text = path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return []
    events: List[dict] = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _parse_dsh_sessions(
    workdir: Path, since_ns: int = 0, extra_roots: Optional[Sequence[Path]] = None,
) -> tuple[List[dict], tuple[int, int, int], int]:
    """Recover provider usage from DSH's durable session log.

    DSH writes both an early ``assistant/chunk`` usage event and a final
    ``assistant/message`` carrying the same sample.  Select the final sample
    per (turn, step) so usage is not double-counted, while retaining the chunk
    sample when a turn fails before a final message is appended.
    """
    roots = [
        Path(workdir) / ".sessions",
        Path(workdir) / ".dsh" / "sessions",
    ]
    # A transaction runs the external harness through try/OverlayFS.  DSH's
    # DSH_HOME therefore lands in the mounted upperdir rather than the host
    # workdir.  Callers pass that upperdir (or sandbox root) here so durable
    # usage remains observable without weakening the isolation boundary.
    roots.extend(Path(root) for root in (extra_roots or ()) if root)
    paths: List[Path] = []
    seen_paths: set[Path] = set()
    for root in roots:
        if root.is_dir():
            for pattern in ("session.jsonl", "session.jsonl.zstd"):
                for path in root.rglob(pattern):
                    try:
                        fresh = path.stat().st_mtime_ns >= since_ns
                    except OSError:
                        continue
                    if fresh and path not in seen_paths:
                        paths.append(path)
                        seen_paths.add(path)
    if not paths:
        return [], (0, 0, 0), 0
    events: List[dict] = []
    for path in sorted(paths, key=lambda candidate: candidate.stat().st_mtime_ns):
        events.extend(_read_session_jsonl(path))
    samples: Dict[tuple[int, int], Dict[str, Any]] = {}
    fallback: List[dict] = []
    tool_calls = 0
    for event in events:
        kind = str(event.get("type", ""))
        data = event.get("data") if isinstance(event.get("data"), dict) else {}
        if kind == "assistant/chunk":
            chunk = data.get("chunk") if isinstance(data.get("chunk"), dict) else {}
            if chunk.get("type") == "usage" and isinstance(chunk.get("usage"), dict):
                key = (int(data.get("turn", 0)), int(data.get("step", 0)))
                samples.setdefault(key, {})["chunk"] = chunk["usage"]
        elif kind == "assistant/message" and isinstance(data.get("usage"), dict):
            key = (int(data.get("turn", 0)), int(data.get("step", 0)))
            samples.setdefault(key, {})["message"] = data["usage"]
        elif kind == "tool/call":
            tool_calls += 1
        elif "usage" in event:
            fallback.append(event)
    usage = [0, 0, 0]
    if samples:
        for sample in samples.values():
            found = _token_usage({"usage": sample.get("message", sample.get("chunk", {}))})
            usage = [left + right for left, right in zip(usage, found)]
    else:
        for event in fallback:
            found = _token_usage(event)
            usage = [left + right for left, right in zip(usage, found)]
    return events, tuple(usage), tool_calls  # type: ignore[return-value]


def _parse_external_output(
    stdout: str, workdir: Path, harness: str, since_ns: int = 0,
    extra_roots: Optional[Sequence[Path]] = None,
) -> tuple[List[dict], tuple[int, int, int], int, str]:
    """Parse stdout and, for DSH, reconcile it with durable usage events."""
    stdout_events, stdout_usage, stdout_tools = _parse_jsonl(stdout)
    if harness != "deepseek_harness":
        return stdout_events, stdout_usage, stdout_tools, "stdout_jsonl"
    session_events, session_usage, session_tools = _parse_dsh_sessions(
        workdir, since_ns, extra_roots=extra_roots
    )
    if session_usage != (0, 0, 0):
        return session_events, session_usage, session_tools, "dsh_session_jsonl"
    return stdout_events, stdout_usage, stdout_tools, "stdout_jsonl"


class ExternalHarness:
    """Small common seam shared by DeepSeek Harness and Codex adapters."""

    name = "external"

    def __init__(
        self,
        model: Optional[str] = None,
        timeout_s: float = 1800.0,
        proxy_command: Optional[str] = None,
    ) -> None:
        self.model = model or "unknown"
        self.timeout_s = float(timeout_s)
        self.proxy_command = self._resolve_proxy_command(proxy_command)

    @staticmethod
    def _resolve_proxy_command(value: Optional[str]) -> List[str]:
        """Return the optional proxy launcher as argv, without shell parsing."""
        if os.environ.get("AGENTTX_NO_PROXY", "").strip().lower() in {
            "1", "true", "yes", "on"
        }:
            return []
        configured = value or os.environ.get("AGENTTX_CLASH_COMMAND")
        if configured:
            return shlex.split(configured)
        for candidate in (
            "/home/pengpeng/.local/bin/agentTX-clash",
            "agentTX-clash",
        ):
            if Path(candidate).exists() or shutil.which(candidate):
                return [candidate]
        return []

    def _with_proxy(self, argv: Sequence[str]) -> List[str]:
        """Run every external harness through the configured Clash launcher."""
        if not self.proxy_command:
            return list(argv)
        return [*self.proxy_command, "run", "--", *argv]

    def command(self, task: str, workdir: Path) -> List[str]:
        raise NotImplementedError

    def preflight(self, workdir: Path) -> Dict[str, Any]:
        command = self.command("preflight", workdir)
        executable = command[0]
        return {
            "harness": self.name,
            "model": self.model,
            "executable": executable,
            "available": bool(shutil.which(executable) or Path(executable).exists()),
            "proxy": self.proxy_command or None,
            "proxy_available": bool(
                not self.proxy_command
                or shutil.which(self.proxy_command[0])
                or Path(self.proxy_command[0]).exists()
            ),
            "command": command,
        }

    def run(self, task: str, workdir: Path, env: Optional[Dict[str, str]] = None) -> ExternalHarnessResult:
        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)
        started = time.perf_counter()
        started_ns = time.time_ns()
        try:
            completed = subprocess.run(
                self.command(task, workdir),
                cwd=str(workdir),
                env=merged_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=self.timeout_s,
                check=False,
            )
            stdout, stderr, returncode = completed.stdout, completed.stderr, completed.returncode
        except subprocess.TimeoutExpired as exc:
            stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
            stderr = (exc.stderr or "") if isinstance(exc.stderr, str) else ""
            returncode = 124
            stderr = (stderr + f"\nexternal harness timeout after {self.timeout_s:.1f}s").strip()
        except OSError as exc:
            stdout, stderr, returncode = "", f"{type(exc).__name__}: {exc}", 127
        duration = time.perf_counter() - started
        events, usage, tool_calls, usage_source = _parse_external_output(
            stdout, Path(workdir), self.name, started_ns
        )
        return ExternalHarnessResult(
            harness=self.name,
            model=self.model,
            returncode=returncode,
            duration_s=duration,
            stdout=stdout[-12000:],
            stderr=stderr[-12000:],
            prompt_tokens=usage[0],
            completion_tokens=usage[1],
            total_tokens=usage[2],
            tool_calls=tool_calls,
            usage_source=usage_source,
            events=events,
        )

    def run_in_transaction(self, transaction_harness: Any, task: str) -> ExternalHarnessResult:
        """Run the external process inside an existing AgentTX overlay.

        The external harness owns the agent loop.  AgentTX supplies the
        protected namespace and records this process as one task boundary;
        adapters can later replace this method with a turn-event bridge without
        changing the benchmark driver.
        """
        workdir = Path(transaction_harness.workdir).resolve()
        started = time.perf_counter()
        started_ns = time.time_ns()
        try:
            record = transaction_harness.tx.run_tool(
                f"external:{self.name}",
                self.command(task, workdir),
                trace_reads=True,
                timeout_s=self.timeout_s,
            )
            stdout, stderr, returncode = record.stdout, record.stderr, record.returncode
        except Exception as exc:  # keep the benchmark row instead of hiding startup failures
            stdout, stderr, returncode = "", f"{type(exc).__name__}: {exc}", 127
        duration = time.perf_counter() - started
        # DSH writes its session log inside the OverlayFS mount.  Include the
        # transaction's upperdir/sandbox roots when reconciling usage so the
        # host-side benchmark row sees the same durable accounting as DSH.
        extra_roots: List[Path] = []
        tx = getattr(transaction_harness, "tx", None)
        pool = getattr(tx, "pool", None)
        for owner in (pool, getattr(pool, "layers", None), tx):
            for attr in ("upperdir", "sandbox_dir", "session_dir"):
                candidate = getattr(owner, attr, None) if owner is not None else None
                if candidate:
                    path = Path(candidate)
                    if path not in extra_roots:
                        extra_roots.append(path)
        events, usage, tool_calls, usage_source = _parse_external_output(
            stdout, workdir, self.name, started_ns, extra_roots=extra_roots
        )
        return ExternalHarnessResult(
            harness=self.name,
            model=self.model,
            returncode=returncode,
            duration_s=duration,
            stdout=stdout[-12000:],
            stderr=stderr[-12000:],
            prompt_tokens=usage[0],
            completion_tokens=usage[1],
            total_tokens=usage[2],
            tool_calls=tool_calls,
            usage_source=usage_source,
            events=events,
        )


class DeepSeekHarness(ExternalHarness):
    """Run the real DeepSeek Harness headless profile."""

    name = "deepseek_harness"

    def __init__(
        self,
        root: Optional[Path] = None,
        model: Optional[str] = None,
        profile: str = "headless",
        command: Optional[str] = None,
        proxy_command: Optional[str] = None,
        timeout_s: float = 1800.0,
    ) -> None:
        load_provider_env(root)
        self.root = Path(
            root
            or os.environ.get("DEEPSEEK_HARNESS_ROOT", "/home/pengpeng/deepseek-harness")
        ).resolve()
        self.profile = profile
        self.command_override = command or os.environ.get("DEEPSEEK_HARNESS_COMMAND")
        self.binary = os.environ.get("DEEPSEEK_HARNESS_BIN")
        if not self.binary:
            self.binary = (
                "/home/pengpeng/.local/bin/dsh"
                if Path("/home/pengpeng/.local/bin/dsh").exists()
                else "dsh"
            )
        super().__init__(
            # Keep the application evaluation on the inexpensive, fixed
            # flash tier.  A CLI --model argument remains an explicit escape
            # hatch for a separately declared model sweep.
            model=model or "deepseek-v4-flash",
            timeout_s=timeout_s,
            proxy_command=proxy_command,
        )

    def command(self, task: str, workdir: Path) -> List[str]:
        if self.command_override:
            prefix = shlex.split(self.command_override)
        else:
            prefix = [self.binary, "--profile", self.profile]
        # The wrapper changes directory to the AgentTX workspace before
        # starting dsh.  This keeps the external harness black-box while
        # ensuring all of its local tools see the protected overlay.
        shell = [
            "bash",
            "-lc",
            # DeepSeek Harness persists profiles and session JSONL under
            # DSH_HOME/DSH_SESSION_ROOT.  Keep both roots inside the protected
            # workspace so AgentTX never tries to publish harness metadata from
            # the user's real home directory.
            f"cd {shlex.quote(str(workdir))} && "
            "mkdir -p .dsh .agents .sessions && "
            "if [ -d /home/pengpeng/.dsh/profiles ] && [ ! -d .dsh/profiles ]; "
            "then cp -a /home/pengpeng/.dsh/profiles .dsh/; fi && "
            "if [ ! -e .dsh/profiles/node_modules ] && "
            "[ -d /home/pengpeng/.dsh/profiles/node_modules ]; "
            "then ln -s /home/pengpeng/.dsh/profiles/node_modules "
            ".dsh/profiles/node_modules; fi && "
            f"DSH_HOME={shlex.quote(str(workdir / '.dsh'))} "
            f"DSH_AGENTS_HOME={shlex.quote(str(workdir / '.agents'))} "
            f"DSH_SESSION_ROOT={shlex.quote(str(workdir / '.sessions'))} "
            f"DEEPSEEK_MODEL={shlex.quote(self.model)} "
            f"DSH_MODEL={shlex.quote(self.model)} "
            f"DSH_SNAPSHOT=1 AGENTTX_DSH_USAGE=1 {shlex.join(prefix + [task])}",
        ]
        return self._with_proxy(shell)

    def preflight(self, workdir: Path) -> Dict[str, Any]:
        result = super().preflight(workdir)
        command = shlex.split(self.command_override)[0] if self.command_override else self.binary
        result.update(
            {
                "root": str(self.root),
                "root_exists": self.root.is_dir(),
                "api_key_configured": bool(os.environ.get("DEEPSEEK_API_KEY")),
                "profile": self.profile,
                "command_available": bool(shutil.which(command) or Path(command).exists()),
            }
        )
        result["available"] = bool(
            result["root_exists"]
            and result["api_key_configured"]
            and shutil.which("bash")
            and result["command_available"]
            and result["proxy_available"]
        )
        return result


class CodexHarness(ExternalHarness):
    """Run the official Codex CLI in unattended execution mode."""

    name = "codex"

    def __init__(
        self,
        model: Optional[str] = None,
        command: Optional[str] = None,
        proxy_command: Optional[str] = None,
        timeout_s: float = 1800.0,
    ) -> None:
        super().__init__(
            # The default Codex evaluation tier is fixed to Luna; an explicit
            # --model argument is still honored for model-matrix experiments.
            model=model or "gpt-5.6-luna",
            timeout_s=timeout_s,
            proxy_command=proxy_command,
        )
        self.command_override = command or os.environ.get("CODEX_COMMAND")
        self.binary = os.environ.get("CODEX_BIN")
        if not self.binary:
            self.binary = (
                "/home/pengpeng/.local/bin/codex"
                if Path("/home/pengpeng/.local/bin/codex").exists()
                else "codex"
            )

    def command(self, task: str, workdir: Path) -> List[str]:
        prefix = shlex.split(self.command_override) if self.command_override else [
            self.binary,
            "exec",
            "--model",
            self.model,
            # AgentTX supplies the outer filesystem sandbox.  The Codex CLI
            # option below avoids an interactive approval protocol; combining
            # --approve-for-me with --sandbox is rejected by current Codex
            # releases.
            "--dangerously-bypass-approvals-and-sandbox",
            "--skip-git-repo-check",
            "--json",
        ]
        # Codex creates its own temporary command wrappers and lock files.
        # Keep CODEX_HOME inside the protected workspace; otherwise the
        # transaction commit policy quite correctly rejects ~/.codex/tmp as
        # an external host write even though the agent was sandboxed.
        codex_home = workdir / ".codex"
        shell = [
            "bash",
            "-lc",
            f"cd {shlex.quote(str(workdir))} && "
            "mkdir -p .codex && "
            # A ChatGPT OAuth login normally lives in ~/.codex/auth.json.
            # Read it through a workspace-local symlink so the CLI can use
            # the subscription allowance without placing its temp/session
            # writes in the host home directory.  The EXIT trap removes the
            # symlink before AgentTX publishes the workspace.
            'CODEX_AUTH_SOURCE="${CODEX_AUTH_FILE:-$HOME/.codex/auth.json}" && '
            'if [ -r "$CODEX_AUTH_SOURCE" ] && [ ! -e .codex/auth.json ]; then '
            'ln -s "$CODEX_AUTH_SOURCE" .codex/auth.json; '
            "trap 'rm -f .codex/auth.json' EXIT; fi && "
            # Prefer an available ChatGPT login for the subscription-backed
            # path.  Set CODEX_AUTH_MODE=api to force API-key billing.
            'if [ "${CODEX_AUTH_MODE:-auto}" != "api" ] && [ -r .codex/auth.json ]; '
            'then unset CODEX_API_KEY OPENAI_API_KEY; '
            'else export CODEX_API_KEY="${CODEX_API_KEY:-${OPENAI_API_KEY:-}}"; fi && '
            # The transaction worker owns its stdin pipe.  Feed an explicit
            # EOF to Codex so its non-interactive `exec` mode cannot wait for
            # an additional prompt after the initial task argument.
            "printf '' | "
            f"CODEX_HOME={shlex.quote(str(codex_home))} "
            f"OPENAI_MODEL={shlex.quote(self.model)} "
            f"{shlex.join(prefix + [task])}",
        ]
        return self._with_proxy(shell)

    def preflight(self, workdir: Path) -> Dict[str, Any]:
        result = super().preflight(workdir)
        command = shlex.split(self.command_override)[0] if self.command_override else self.binary
        result["command_available"] = bool(shutil.which(command) or Path(command).exists())
        auth_candidates = [
            Path(os.environ.get("CODEX_AUTH_FILE", "")),
            Path(os.environ.get("CODEX_HOME", "")) / "auth.json",
            Path(os.environ.get("HOME", "")) / ".codex" / "auth.json",
        ]
        chatgpt_auth = any(path.is_file() for path in auth_candidates if str(path) not in {"", "."})
        result["api_key_configured"] = bool(
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("CODEX_API_KEY")
            or chatgpt_auth
        )
        result["chatgpt_auth_configured"] = chatgpt_auth
        result["available"] = bool(
            result["available"]
            and result["command_available"]
            and result["api_key_configured"]
            and result["proxy_available"]
        )
        return result


def create_external_harness(
    name: str,
    *,
    root: Optional[Path] = None,
    model: Optional[str] = None,
    command: Optional[str] = None,
    proxy_command: Optional[str] = None,
    timeout_s: float = 1800.0,
) -> ExternalHarness:
    normalized = name.lower().replace("-", "_")
    if normalized in {"deepseek", "deepseek_harness", "dsh"}:
        return DeepSeekHarness(
            root=root,
            model=model,
            command=command,
            proxy_command=proxy_command,
            timeout_s=timeout_s,
        )
    if normalized in {"codex", "codex_harness"}:
        return CodexHarness(
            model=model,
            command=command,
            proxy_command=proxy_command,
            timeout_s=timeout_s,
        )
    raise ValueError("unknown external harness {!r}; choose deepseek_harness or codex".format(name))
