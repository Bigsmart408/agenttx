"""Shared/incremental semisolate pool backed by binpash/try -N DIR."""
from __future__ import annotations
import base64, hashlib, json, os, re, shlex, shutil, signal, stat, struct, subprocess, tempfile, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterator, List, Optional, Sequence, Tuple
from . import bpf_trace
from .effects import SummaryEntry, diff_summaries, parse_try_summary
from .ledger import Effect, EffectKind
from .layers import LayerStore, _remove_overlay_tree
from .trace import parse_strace_effects

_WHITEOUT_DIGEST = "<agenttx:whiteout>"
_TRACE_BACKENDS = ("auto", "strace", "bpf")
_BPF_MARKER_PREFIX = ".agenttx-bpf-marker-"
_BPF_MARKER_HOLD = "hold"
_BPF_MARKER_GO = "go"


def _iter_upper_entries(directory: Path) -> Iterator[Path]:
    """Walk an unmounted upperdir without losing mode-000 descendants."""
    original_mode = stat.S_IMODE(directory.lstat().st_mode)
    access_mode = original_mode | stat.S_IRUSR | stat.S_IXUSR
    changed_mode = access_mode != original_mode
    if changed_mode:
        directory.chmod(access_mode)
    try:
        with os.scandir(directory) as scan:
            entries = list(scan)
        for entry in entries:
            path = Path(entry.path)
            mode = entry.stat(follow_symlinks=False).st_mode
            yield path
            if stat.S_ISDIR(mode):
                yield from _iter_upper_entries(path)
    finally:
        if changed_mode:
            directory.chmod(original_mode)


def _grant_upper_commit_access(
    directory: Path, modes: Dict[Path, int]
) -> None:
    directory_stat = directory.lstat()
    directory_mode = stat.S_IMODE(directory_stat.st_mode)
    modes[directory] = directory_mode
    accessible_mode = (
        directory_mode | stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR
    )
    if accessible_mode != directory_mode:
        directory.chmod(accessible_mode)

    with os.scandir(directory) as scan:
        entries = list(scan)
    for entry in entries:
        path = Path(entry.path)
        entry_mode = entry.stat(follow_symlinks=False).st_mode
        if stat.S_ISDIR(entry_mode):
            _grant_upper_commit_access(path, modes)
        elif stat.S_ISREG(entry_mode):
            original_mode = stat.S_IMODE(entry_mode)
            modes[path] = original_mode
            readable_mode = original_mode | stat.S_IRUSR
            if readable_mode != original_mode:
                path.chmod(readable_mode)


def _restore_upper_modes(modes: Dict[Path, int]) -> None:
    for path, mode in sorted(
        modes.items(),
        key=lambda item: len(item[0].parts),
        reverse=True,
    ):
        try:
            path.chmod(mode)
        except FileNotFoundError:
            continue


def _read_regular_preserving_mode(path: Path, mode: int) -> bytes:
    original_mode = stat.S_IMODE(mode)
    readable_mode = original_mode | stat.S_IRUSR
    changed_mode = readable_mode != original_mode
    if changed_mode:
        path.chmod(readable_mode)
    try:
        return path.read_bytes()
    finally:
        if changed_mode:
            path.chmod(original_mode)


def _default_try_bin() -> Path:
    root = Path(__file__).resolve().parents[2]
    wrapper = root / "scripts" / "try-wrapper.sh"
    if wrapper.exists():
        return wrapper
    raise FileNotFoundError("scripts/try-wrapper.sh not found")


def _probe_try_backend(try_bin: Path, workspace: Path) -> tuple[bool, str]:
    """Return whether try can execute one no-op in this host namespace."""
    probe = Path(tempfile.mkdtemp(prefix="agenttx-try-probe-", dir="/tmp"))
    sandbox = probe / "sandbox"
    sandbox.mkdir()
    try:
        env = {**os.environ, "PWD": str(workspace)}
        result = subprocess.run(
            [str(try_bin), "-N", str(sandbox), "--", "/bin/true"],
            cwd=str(workspace), env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, timeout=30, check=False,
        )
        detail = (result.stderr or result.stdout or "").strip().splitlines()
        return result.returncode == 0, detail[-1] if detail else f"rc={result.returncode}"
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    finally:
        shutil.rmtree(probe, ignore_errors=True)

def _descendant_pids(root: int) -> set:
    """Return `root` and every live descendant pid, walked from /proc.

    The eBPF tracer's syscall tracepoints are global, so the userspace parser
    needs the seed's process tree to filter events.  The walk runs when the
    probes have attached (ATXBPF_READY): the try sandbox's setup forks happen
    before attach and are invisible to in-kernel fork tracking, so they must
    be discovered here; forks after the snapshot are recovered from traced
    clone/fork/vfork/clone3 exits in the parser.
    """
    seen = {root}
    frontier = [root]
    while frontier:
        pid = frontier.pop()
        try:
            tids = os.listdir(f"/proc/{pid}/task")
        except OSError:
            continue
        for tid in tids:
            try:
                with open(f"/proc/{pid}/task/{tid}/children", "r") as handle:
                    children = [int(item) for item in handle.read().split()]
            except OSError:
                continue
            for child in children:
                if child not in seen:
                    seen.add(child)
                    frontier.append(child)
    return seen


@dataclass
class StepResult:
    step_index: int
    returncode: int
    stdout: str
    stderr: str
    summary_before: Dict[str, SummaryEntry]
    summary_after: Dict[str, SummaryEntry]
    duration_s: float
    effects: List[Effect] = field(default_factory=list)
    tracer: Optional[str] = None

@dataclass
class SharedSemisolate:
    workspace: Path
    try_bin: Path = field(default_factory=_default_try_bin)
    sandbox_dir: Optional[Path] = None
    hide_network: bool = False
    trace_reads: bool = True
    trace_backend: str = "auto"
    _owns_sandbox: bool = False
    _step_count: int = 0
    _closed: bool = False
    _cached_summary: Dict[str, SummaryEntry] = field(default_factory=dict)
    _cached_digests: Dict[str, str] = field(default_factory=dict)
    _cmd_script: Optional[Path] = None
    persistent_worker: bool = True
    _worker_process: Optional[subprocess.Popen] = None
    _worker_script: Optional[Path] = None
    _pending_snapshot_changes: Optional[List[str]] = None
    _worker_crash_once: bool = False
    _worker_failure_count: int = 0
    _bpf_state: Optional[dict] = None
    layers: Optional[LayerStore] = None

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        self.try_bin = Path(self.try_bin)
        if self.trace_backend not in _TRACE_BACKENDS:
            raise ValueError(
                f"trace_backend must be one of {_TRACE_BACKENDS}, "
                f"got {self.trace_backend!r}"
            )
        if self.trace_reads:
            strace_present = shutil.which("strace") is not None
            if self.trace_backend == "strace" and not strace_present:
                raise RuntimeError(
                    "automatic dependency tracing requires strace; "
                    "construct SharedSemisolate(trace_reads=False) to opt out"
                )
            if self.trace_backend == "bpf":
                static_ok, static_detail = bpf_trace.bpf_static_available()
                if not static_ok:
                    raise RuntimeError(
                        "automatic dependency tracing requires a working eBPF "
                        f"tracer; construct SharedSemisolate(trace_reads=False) "
                        f"to opt out. bpf probe: {static_detail}"
                    )
            elif not strace_present and not bpf_trace.bpf_static_available()[0]:
                raise RuntimeError(
                    "automatic dependency tracing requires strace or a "
                    "working eBPF tracer; construct "
                    "SharedSemisolate(trace_reads=False) to opt out"
                )
        if self.sandbox_dir is None:
            self.sandbox_dir = Path(tempfile.mkdtemp(prefix="agenttx-sandbox-", dir="/tmp"))
            self._owns_sandbox = True
        if self.layers is None:
            self.layers = LayerStore(self.sandbox_dir / "layers")
        else:
            self.sandbox_dir = Path(self.sandbox_dir)
            self.sandbox_dir.mkdir(parents=True, exist_ok=True)

        try_ok, detail = _probe_try_backend(self.try_bin, self.workspace)
        if not try_ok:
            raise RuntimeError(
                "try overlay backend is unavailable; run the AgentTX runtime "
                f"with root on this host. try probe: {detail}"
            )
        if self.layers is None:
            self.layers = LayerStore(self.sandbox_dir / "layers")

    @property
    def session_dir(self) -> Path:
        assert self.sandbox_dir is not None
        return self.sandbox_dir

    def _run_try(self, args: Sequence[str], cwd: Optional[Path] = None) -> subprocess.CompletedProcess:
        start_dir = Path(cwd or self.workspace).resolve()
        env = os.environ.copy()
        # Upstream try records its chroot START_DIR from the shell's $PWD.
        # subprocess cwd= changes the kernel cwd but deliberately leaves the
        # inherited environment untouched, so a stale PWD would make commands
        # start in the benchmark runner/repository instead of the workspace.
        env["PWD"] = str(start_dir)
        return subprocess.run(
            [str(self.try_bin), *args],
            cwd=str(start_dir),
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )

    # ------------------------------------------------------------------
    # Trace backend selection (strace vs eBPF)
    # ------------------------------------------------------------------

    def _resolve_step_backend(self) -> Tuple[str, str]:
        """Choose the tracing backend for this step; fails closed."""
        if self.trace_backend == "strace":
            return "strace", "strace"
        self._ensure_bpf_state()
        assert self._bpf_state is not None
        if self._bpf_state["available"]:
            return "bpf", self._bpf_state["detail"]
        if self.trace_backend == "bpf":
            raise RuntimeError(
                "trace backend 'bpf' requested but eBPF tracing is "
                f"unavailable: {self._bpf_state['detail']}"
            )
        return "strace", "strace (eBPF unavailable)"

    def _ensure_bpf_state(self) -> None:
        """Attach-precheck the eBPF tracer once per session and cache it."""
        if self._bpf_state is not None:
            return
        script, resolved, detail = bpf_trace.resolve_bpf_script()
        self._bpf_state = {
            "available": bool(script),
            "script": script,
            "resolved": resolved,
            "detail": detail,
        }

    # ------------------------------------------------------------------
    # eBPF-traced step execution
    # ------------------------------------------------------------------

    def _run_step_bpf(
        self, command: Sequence[str], flags: Sequence[str]
    ) -> Tuple[subprocess.CompletedProcess, List[Effect], float]:
        """Run one step under the eBPF tracer.

        The command is held on a release-marker file until bpftrace reports
        ``ATXBPF_READY``, so no syscall of the command (or its descendants)
        can escape the trace.  A tracer failure fails the step closed after
        the command has run (releasing the hold first), mirroring the strace
        backend's missing-log behavior; the host stays clean because effects
        remain speculative in the overlay.
        """
        self._ensure_bpf_state()
        assert self._bpf_state is not None and self._bpf_state["script"]
        assert self.sandbox_dir is not None
        session_token = hashlib.sha256(
            str(self.sandbox_dir).encode("utf-8")
        ).hexdigest()[:12]
        tag = f"{session_token}-{os.getpid()}-{self._step_count}"
        marker_name = f"{_BPF_MARKER_PREFIX}{tag}"
        marker_logical = Path("/tmp") / marker_name
        marker_upper = self.sandbox_dir / "upperdir" / "tmp" / marker_name
        trace_log = Path("/tmp") / f".agenttx-bpf-{tag}.raw"
        marker_upper.parent.mkdir(parents=True, exist_ok=True)
        # Pre-create the marker with a "hold" payload: the command polls the
        # marker's CONTENT (not its existence).  Existence polling would race
        # OverlayFS negative-dentry caching — a lookup that misses before the
        # file is created stays cached as ENOENT even after the file appears
        # in the upperdir (verified on this kernel).
        marker_upper.write_text(_BPF_MARKER_HOLD)
        bpf_proc: Optional[subprocess.Popen] = None
        log_handle = None
        released = False
        ready_failed: Optional[str] = None
        traced_tree: Optional[set] = None
        popen: Optional[subprocess.Popen] = None
        collect: Optional[Callable[[], subprocess.CompletedProcess]] = None
        waiter: Optional[subprocess.Popen] = None
        t0 = time.perf_counter()
        try:
            if self.persistent_worker:
                request = {
                    "argv": list(command),
                    "cwd": str(self.workspace),
                    "hold_marker": str(marker_logical),
                }
                try:
                    self._dispatch_worker(request)
                except Exception:
                    # Worker failure must not change correctness: fall back to
                    # the one-shot try path, exactly like the strace backend.
                    self._worker_failure_count += 1
                    self._stop_worker()
                    self._repair_worker_sandbox()
                else:
                    assert self._worker_process is not None
                    waiter = self._worker_process
                    collect = lambda: self._collect_worker_response(list(command))
            if collect is None:
                # The hold runs from a script file: try word-splits inline
                # `bash -c` strings when it builds script_to_execute.sh.
                # FIFO pipe pairing does not cross the OverlayFS mount
                # boundary either (pipes are allocated against the
                # superblock's user namespace), so the script polls the
                # release marker's content instead.
                popen = subprocess.Popen(
                    [
                        str(self.try_bin),
                        *flags,
                        "--",
                        str(self._hold_script()),
                        str(marker_logical),
                        *command,
                    ],
                    cwd=str(self.workspace),
                    env={**os.environ, "PWD": str(self.workspace)},
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                waiter = popen
            seed = waiter.pid if waiter is not None else 0
            log_handle = open(trace_log, "w", encoding="utf-8")
            binary = bpf_trace.bpftrace_binary()
            assert binary is not None  # validated during initialization
            bpf_proc = subprocess.Popen(
                [
                    *bpf_trace.bpftrace_quiet_flag(),
                    binary,
                    "-e",
                    self._bpf_state["script"],
                    str(seed),
                ],
                stdout=log_handle,
                stderr=subprocess.DEVNULL,
                env={**os.environ, **bpf_trace.bpftrace_strlen_env()},
            )
            ready, elapsed = bpf_trace.wait_for_bpftrace_ready(
                trace_log,
                seed,
                timeout=30.0,
                abort_check=lambda: bpf_proc.poll() is not None,
            )
            if not ready:
                ready_failed = (
                    f"bpftrace did not become ready within {elapsed:.1f}s "
                    f"(rc={bpf_proc.returncode})"
                )
            else:
                # Snapshot the seed's live descendant tree now: the sandbox's
                # setup forks predate probe attach, and the userspace parser
                # filters the (global) tracepoint events to this tree.
                traced_tree = _descendant_pids(seed)
            self._signal_release(marker_upper)
            released = True
            if collect is not None:
                cp = collect()
            else:
                assert popen is not None
                stdout, stderr = popen.communicate()
                cp = subprocess.CompletedProcess(
                    popen.args, popen.returncode, stdout, stderr
                )
        finally:
            if bpf_proc is not None:
                try:
                    bpf_proc.send_signal(signal.SIGINT)
                    bpf_proc.wait(timeout=10)
                except Exception:
                    bpf_proc.kill()
                    try:
                        bpf_proc.wait(timeout=5)
                    except Exception:
                        pass
            if not released:
                # A readiness failure must not leave the command blocked on
                # the hold; release it so the step can complete and fail
                # closed.
                try:
                    self._signal_release(marker_upper)
                except Exception:
                    pass
            if log_handle is not None:
                log_handle.close()
        duration = time.perf_counter() - t0
        try:
            if ready_failed is not None:
                raise RuntimeError(ready_failed)
            if not trace_log.is_file():
                raise RuntimeError(
                    "bpftrace did not produce the expected dependency log"
                )
            text = trace_log.read_text(encoding="utf-8", errors="replace")
            effects = bpf_trace.parse_bpf_effects(
                text, self.workspace, allowed_pids=traced_tree
            )
            return cp, effects, duration
        finally:
            for leftover in (marker_upper, trace_log):
                try:
                    leftover.unlink(missing_ok=True)
                except OSError:
                    pass

    def _signal_release(self, marker_upper: Path) -> None:
        """Flip the release marker to "go", unblocking the traced command.

        The marker file is pre-created with a "hold" payload in the overlay
        upperdir; the traced command (worker or one-shot bash) polls its
        content through the sandbox's mount.  A FIFO cannot serve this
        handshake: FIFO pipe pairing does not cross the OverlayFS mount
        boundary, because pipes are allocated against the superblock's user
        namespace — a reader inside the sandbox never pairs with a writer on
        the host upperdir (verified on this kernel in both directions).
        """
        marker_upper.write_text(_BPF_MARKER_GO)

    def refresh_summary(self) -> Dict[str, SummaryEntry]:
        assert self.sandbox_dir is not None
        try:
            if not any(self.sandbox_dir.iterdir()):
                self._cached_summary = {}
                return self._cached_summary
        except FileNotFoundError:
            self._cached_summary = {}
            return self._cached_summary
        self._stop_worker()
        cp = self._run_try(["summary", str(self.sandbox_dir)])
        self._cached_summary = parse_try_summary(cp.stdout)
        return self._cached_summary

    def upperdir_digests(self) -> Dict[str, str]:
        """Fingerprint materialized overlay entries relevant to the ledger.

        File fingerprints include metadata so repeated chmod/chown/touch calls are
        visible even when content is unchanged. Directories are tracked only below
        the transaction workspace; this excludes try's root-level mount scaffolding
        while preserving empty-directory effects created by agent tools.
        """
        assert self.sandbox_dir is not None
        upper = self.sandbox_dir / "upperdir"
        out: Dict[str, str] = {}
        if not upper.exists():
            return out
        for entry in _iter_upper_entries(upper):
            try:
                entry_stat = entry.lstat()
                mode = entry_stat.st_mode
                rel = entry.relative_to(upper)
                logical = Path("/" + rel.as_posix())

                # Read tracing writes an internal raw log into /tmp inside the
                # overlay, and the eBPF backend leaves a release marker there.
                # Ignore crash leftovers and never expose them as effects.
                if rel.parent == Path("tmp") and (
                    entry.name.startswith(".agenttx-strace-")
                    or entry.name.startswith(_BPF_MARKER_PREFIX)
                ):
                    continue

                # OverlayFS uses character devices as whiteouts on this VM. Some
                # union implementations use .wh.<name> files instead.
                if entry.name == ".wh..wh..opq":
                    logical = Path("/" + rel.parent.as_posix())
                    out[str(logical)] = "<agenttx:opaque-directory>"
                    continue
                if entry.name.startswith(".wh."):
                    logical = logical.with_name(entry.name[4:])
                    out[str(logical)] = _WHITEOUT_DIGEST
                    continue
                if stat.S_ISCHR(mode):
                    out[str(logical)] = _WHITEOUT_DIGEST
                    continue

                metadata = (
                    f"{stat.S_IMODE(mode):o}:{entry_stat.st_uid}:"
                    f"{entry_stat.st_gid}:{entry_stat.st_mtime_ns}"
                ).encode("ascii")
                if stat.S_ISLNK(mode):
                    target = os.readlink(str(entry)).encode("utf-8", "surrogateescape")
                    payload = b"link\0" + metadata + b"\0" + target
                elif stat.S_ISREG(mode):
                    payload = (
                        b"file\0"
                        + metadata
                        + b"\0"
                        + _read_regular_preserving_mode(entry, mode)
                    )
                elif stat.S_ISDIR(mode):
                    try:
                        logical.relative_to(self.workspace)
                    except ValueError:
                        continue
                    if logical == self.workspace:
                        continue
                    # Child creation changes directory mtime, but the child itself
                    # is already an effect. Excluding mtime avoids spurious rewrites.
                    directory_metadata = (
                        f"{stat.S_IMODE(mode):o}:{entry_stat.st_uid}:{entry_stat.st_gid}"
                    ).encode("ascii")
                    payload = b"directory\0" + directory_metadata
                else:
                    continue
                out[str(logical)] = hashlib.sha256(payload).hexdigest()
            except OSError:
                continue
        return out

    def _write_cmd_script(self, argv: Sequence[str]) -> Path:
        # Reuse one private script per semisolate.  try still needs a stable
        # executable path, but creating a temporary directory, chmod-ing, and
        # recursively deleting it on every step dominated short commands.
        if self._cmd_script is None:
            cmd_dir = Path(tempfile.mkdtemp(prefix="agenttx-cmd-", dir="/tmp"))
            self._cmd_script = cmd_dir / "cmd.sh"
            self._cmd_script.touch(mode=0o700)
        script = self._cmd_script
        if len(argv) >= 3 and Path(argv[0]).name == "bash" and argv[1] == "-c":
            body = "#!/bin/bash\nset -e\n" + argv[2] + "\n"
        else:
            body = "#!/bin/bash\nset -e\n" + " ".join(shlex.quote(a) for a in argv) + "\n"
        script.write_text(body, encoding="utf-8")
        return script

    def _hold_script(self) -> Path:
        """Return the eBPF hold script used by the one-shot try path.

        The hold must be a script file, not an inline ``bash -c`` string:
        try word-splits inline arguments when it builds script_to_execute.sh
        (verified on this host), so semicolons and quotes in the hold logic
        would be mangled.  The script lives next to the command script, which
        is already visible inside the sandbox via the /tmp overlay.
        """
        parent = (
            self._cmd_script.parent
            if self._cmd_script is not None
            else Path(tempfile.mkdtemp(prefix="agenttx-cmd-", dir="/tmp"))
        )
        hold = parent / "hold.sh"
        if not hold.exists():
            hold.write_text(
                "#!/bin/bash\n"
                "# AgentTX eBPF hold: wait for the release marker, then exec.\n"
                'while [ "$(cat "$1" 2>/dev/null)" != "go" ]; '
                "do sleep 0.01; done\n"
                "shift\n"
                'exec "$@"\n',
                encoding="utf-8",
            )
            hold.chmod(0o700)
        return hold

    def _ensure_worker(self) -> None:
        if self._worker_process is not None and self._worker_process.poll() is None:
            return
        if self._cmd_script is None:
            raise RuntimeError("command script must exist before starting worker")
        worker_source = Path(__file__).with_name("try_worker.py")
        self._worker_script = self._cmd_script.parent / "worker.py"
        self._worker_script.write_bytes(worker_source.read_bytes())
        self._worker_script.chmod(0o700)
        assert self.sandbox_dir is not None
        flags = ["-N", str(self.sandbox_dir)]
        if self.hide_network:
            flags.insert(0, "-x")
        process = subprocess.Popen(
            [str(self.try_bin), *flags, "--", "python3", str(self._worker_script)],
            cwd=str(self.workspace),
            env={**os.environ, "PWD": str(self.workspace)},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._worker_process = process
        assert process.stdin is not None and process.stdout is not None
        try:
            ready = self._read_worker_frame(process.stdout)
            if ready.get("ready") is not True:
                raise RuntimeError("try worker did not become ready")
        except Exception:
            self._stop_worker()
            raise

    def inject_worker_crash_once(self) -> None:
        """Inject one persistent-worker failure before its next request.

        This is an evaluation hook, not a normal execution path.  Killing the
        worker before dispatch leaves the command for the fail-safe one-shot
        fallback in :meth:`run`, so crash recovery can be measured without
        intentionally duplicating filesystem effects.
        """
        if self._worker_process is None or self._worker_process.poll() is not None:
            raise RuntimeError("try worker is not running")
        self._worker_crash_once = True

    @property
    def worker_failure_count(self) -> int:
        """Number of worker failures that have used the one-shot fallback."""
        return self._worker_failure_count

    @staticmethod
    def _read_worker_frame(stream) -> dict:
        def read_exact(size: int) -> bytes:
            chunks = bytearray()
            while len(chunks) < size:
                chunk = stream.read(size - len(chunks))
                if not chunk:
                    raise RuntimeError("try worker exited before responding")
                chunks.extend(chunk)
            return bytes(chunks)

        header = read_exact(4)
        (size,) = struct.unpack("!I", header)
        if size > 128 * 1024 * 1024:
            raise RuntimeError("try worker response is too large")
        body = read_exact(size)
        value = json.loads(body.decode("utf-8"))
        if not isinstance(value, dict):
            raise RuntimeError("invalid try worker response")
        return value

    def _run_worker(
        self, command: Sequence[str], cwd: Path
    ) -> subprocess.CompletedProcess:
        request = {"argv": list(command), "cwd": str(cwd)}
        self._dispatch_worker(request)
        return self._collect_worker_response(list(command))

    def _dispatch_worker(self, request: dict) -> None:
        self._ensure_worker()
        assert self._worker_process is not None
        assert self._worker_process.stdin is not None
        body = json.dumps(request, separators=(",", ":")).encode("utf-8")
        if self._worker_crash_once:
            self._worker_crash_once = False
            self._worker_process.kill()
            self._worker_process.wait(timeout=2)
            raise RuntimeError("injected persistent-worker crash")
        self._worker_process.stdin.write(struct.pack("!I", len(body)))
        self._worker_process.stdin.write(body)
        self._worker_process.stdin.flush()

    def _collect_worker_response(self, argv: Sequence[str]) -> subprocess.CompletedProcess:
        assert self._worker_process is not None
        assert self._worker_process.stdout is not None
        response = self._read_worker_frame(self._worker_process.stdout)
        if "error" in response:
            raise RuntimeError(str(response["error"]))
        stdout = base64.b64decode(response.get("stdout", ""))
        stderr_value = response.get("stderr", "")
        if isinstance(stderr_value, str) and response.get("returncode") is not None:
            try:
                stderr = base64.b64decode(stderr_value)
            except Exception:
                stderr = stderr_value.encode("utf-8", "replace")
        else:
            stderr = b""
        return subprocess.CompletedProcess(
            list(argv),
            int(response.get("returncode", 1)),
            stdout.decode("utf-8", "replace"),
            stderr.decode("utf-8", "replace"),
        )

    def _stop_worker(self) -> None:
        process = self._worker_process
        self._worker_process = None
        self._worker_script = None
        if process is None:
            return
        try:
            if process.poll() is None and process.stdin is not None:
                request = json.dumps({"op": "shutdown"}, separators=(",", ":")).encode("utf-8")
                process.stdin.write(struct.pack("!I", len(request)))
                process.stdin.write(request)
                process.stdin.flush()
                if process.stdout is not None:
                    self._read_worker_frame(process.stdout)
            process.wait(timeout=2)
        except Exception:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def _repair_worker_sandbox(self) -> None:
        """Remove stale try mount scaffolding after a worker process loss."""
        assert self.sandbox_dir is not None
        # A killed try namespace cannot run its normal unmount/cleanup trap.
        # The upperdir is the speculative state we must retain; all other
        # generated mount scaffolding can be recreated by the one-shot path.
        for name in ("temproot", "workdir", "hidedir", "mergerdir"):
            _remove_overlay_tree(self.sandbox_dir / name)
        for name in (
            "mounts",
            "mounts.updated",
            "mount_and_execute.sh",
            "chroot_executable.sh",
            "script_to_execute.sh",
            "hide",
            "include",
            "exclude",
            "mount.log",
            "error.log",
        ):
            path = self.sandbox_dir / name
            if path.exists() or os.path.lexists(path):
                _remove_overlay_tree(path)
        (self.sandbox_dir / "upperdir").mkdir(parents=True, exist_ok=True)

    def run(
        self, argv: Sequence[str], *, trace_reads: Optional[bool] = None
    ) -> StepResult:
        if self._closed:
            raise RuntimeError("SharedSemisolate is closed")
        assert self.sandbox_dir is not None
        before = dict(self._cached_summary)
        dig_before = dict(self._cached_digests)
        should_trace_reads = (
            self.trace_reads if trace_reads is None else trace_reads
        )
        assert self.layers is not None and self.sandbox_dir is not None
        upper = self.sandbox_dir / "upperdir"
        self.layers.snapshot_before(
            self._step_count,
            upper,
            fingerprints=dig_before,
            changed_paths=self._pending_snapshot_changes,
        )
        flags = ["-N", str(self.sandbox_dir)]
        if self.hide_network:
            flags.insert(0, "-x")
        script = self._write_cmd_script(argv)
        # The script already has a fixed bash shebang and executable mode;
        # executing it directly avoids an extra shell parse in every try call.
        command = [str(script)]
        trace_upper: Optional[Path] = None
        tracer: Optional[str] = None
        trace_effects: List[Effect] = []
        if should_trace_reads:
            backend, _detail = self._resolve_step_backend()
            if backend == "bpf":
                cp, trace_effects, duration = self._run_step_bpf(command, flags)
                tracer = "bpf"
            else:
                tracer = "strace"
                strace_bin = shutil.which("strace")
                assert strace_bin is not None  # validated during initialization
                session_token = hashlib.sha256(
                    str(self.sandbox_dir).encode("utf-8")
                ).hexdigest()[:12]
                trace_name = (
                    f".agenttx-strace-{session_token}-{os.getpid()}-"
                    f"{self._step_count}.raw"
                )
                trace_logical = Path("/tmp") / trace_name
                trace_upper = self.sandbox_dir / "upperdir" / "tmp" / trace_name
                command = [
                    strace_bin,
                    "-yy",
                    "-f",
                    "-s",
                    "4096",
                    "--seccomp-bpf",
                    "--trace=%file,process",
                    "-o",
                    str(trace_logical),
                    *command,
                ]

        if tracer != "bpf":
            t0 = time.perf_counter()
            if self.persistent_worker:
                try:
                    cp = self._run_worker(command, self.workspace)
                except Exception:
                    # A worker failure must not change correctness. Restarting
                    # the worker is deferred; this step uses the original try path.
                    self._worker_failure_count += 1
                    self._stop_worker()
                    self._repair_worker_sandbox()
                    cp = self._run_try([*flags, "--", *command], cwd=self.workspace)
            else:
                cp = self._run_try([*flags, "--", *command], cwd=self.workspace)
            duration = time.perf_counter() - t0

        if trace_upper is not None:
            try:
                if not trace_upper.is_file():
                    raise RuntimeError(
                        "strace did not produce the expected dependency log"
                    )
                trace_effects = parse_strace_effects(
                    trace_upper.read_text(encoding="utf-8", errors="replace"),
                    self.workspace,
                )
            finally:
                trace_upper.unlink(missing_ok=True)

        # Digests alone detect writes/deletes without a second try summary process.
        dig_after = self.upperdir_digests()
        self._cached_digests = dig_after
        effects_by_path = {}
        for path, h in dig_after.items():
            if dig_before.get(path) != h:
                kind = EffectKind.DELETE if h == _WHITEOUT_DIGEST else EffectKind.WRITE
                effects_by_path[path] = Effect(path=path, kind=kind)
        for path in dig_before:
            if path not in dig_after:
                effects_by_path[path] = Effect(path=path, kind=EffectKind.DELETE)
        effects = trace_effects + [
            effects_by_path[path] for path in sorted(effects_by_path)
        ]
        self._pending_snapshot_changes = sorted(
            {
                effect.path
                for effect in effects
                if effect.kind in (EffectKind.WRITE, EffectKind.DELETE)
            }
        )
        # Keep cached summary lazily empty; refresh only on explicit commit/status.
        after = dict(self._cached_summary)
        idx = self._step_count
        self._step_count += 1
        return StepResult(step_index=idx, returncode=cp.returncode, stdout=cp.stdout, stderr=cp.stderr, summary_before=before, summary_after=after, duration_s=duration, effects=effects, tracer=tracer)

    def step_effects(self, result: StepResult) -> List[Effect]:
        return list(result.effects)

    def _include_patterns(self, paths: Sequence[str]) -> List[str]:
        """Build exact suffix regexes for try's upperdir `find` output."""
        patterns = set()
        for raw_path in paths:
            if "\n" in raw_path or "\r" in raw_path:
                raise ValueError("commit paths cannot contain newlines")
            path = Path(raw_path)
            if not path.is_absolute():
                raise ValueError(f"commit path must be absolute: {raw_path}")
            if path == path.parent:
                raise ValueError("refusing to selectively commit the filesystem root")
            # Include exact parents so a newly-created directory can be
            # materialized without accidentally including sibling changes.
            current = path
            while current != current.parent:
                patterns.add(re.escape(str(current)) + "$")
                if current == self.workspace:
                    break
                current = current.parent
        return sorted(patterns, key=lambda pattern: (pattern.count("/"), pattern))

    def _capture_commit_metadata(
        self, paths: Optional[Sequence[str]]
    ) -> Dict[str, tuple[int, int, int, str]]:
        """Capture mode/times before try consumes selected upperdir entries."""
        assert self.sandbox_dir is not None
        wanted = set(paths) if paths is not None else None
        upper = self.sandbox_dir / "upperdir"
        metadata: Dict[str, tuple[int, int, int, str]] = {}
        if not upper.exists():
            return metadata

        for entry in _iter_upper_entries(upper):
            rel = entry.relative_to(upper)
            logical = Path("/" + rel.as_posix())
            try:
                logical.relative_to(self.workspace)
            except ValueError:
                continue
            logical_text = str(logical)
            if wanted is not None and logical_text not in wanted:
                continue

            entry_stat = entry.lstat()
            mode = entry_stat.st_mode
            if stat.S_ISREG(mode):
                kind = "file"
            elif stat.S_ISDIR(mode):
                kind = "directory"
            elif stat.S_ISLNK(mode):
                kind = "symlink"
            else:
                continue
            metadata[logical_text] = (
                stat.S_IMODE(mode),
                entry_stat.st_atime_ns,
                entry_stat.st_mtime_ns,
                kind,
            )
        return metadata

    @staticmethod
    def _restore_committed_metadata(
        metadata: Dict[str, tuple[int, int, int, str]]
    ) -> None:
        # Apply child metadata before making a parent directory non-searchable.
        ordered = sorted(
            metadata.items(),
            key=lambda item: len(Path(item[0]).parts),
            reverse=True,
        )
        for path_text, (mode, atime_ns, mtime_ns, kind) in ordered:
            path = Path(path_text)
            try:
                current_mode = path.lstat().st_mode
            except FileNotFoundError:
                continue
            if kind == "symlink":
                if stat.S_ISLNK(current_mode):
                    os.utime(
                        path,
                        ns=(atime_ns, mtime_ns),
                        follow_symlinks=False,
                    )
                continue
            if kind == "file" and stat.S_ISREG(current_mode):
                os.utime(path, ns=(atime_ns, mtime_ns))
                path.chmod(mode)
            elif kind == "directory" and stat.S_ISDIR(current_mode):
                path.chmod(mode)

    def commit_from_snapshot(
        self, before_step_id: int, paths: Sequence[str]
    ) -> subprocess.CompletedProcess:
        """Commit selected paths from a historical frontier snapshot.

        The current speculative upperdir is copied aside, the snapshot taken
        before the first later step is mounted as the temporary upperdir, and
        the current upperdir is restored after ``try commit`` consumes the
        historical entries. The caller's WAL protects both images if the
        process is interrupted in the middle of this reconstruction.
        """
        self._stop_worker()
        assert self.sandbox_dir is not None and self.layers is not None
        snapshot = self.layers.root / f"before_{before_step_id:04d}"
        if not snapshot.exists():
            raise FileNotFoundError(
                f"historical commit snapshot is missing: {snapshot}"
            )
        upper = self.sandbox_dir / "upperdir"
        temporary = Path(
            tempfile.mkdtemp(prefix=".agenttx-historical-", dir=str(self.sandbox_dir))
        )
        saved = temporary / "current"
        saved_current = False
        try:
            self.layers.copy_tree(upper, saved)
            saved_current = True
            self.layers.copy_tree(snapshot, upper)
            return self.commit(paths=paths)
        finally:
            if saved_current:
                self.layers.copy_tree(saved, upper)
                self._cached_summary = {}
                self._cached_digests = self.upperdir_digests()
                self._pending_snapshot_changes = None
            _remove_overlay_tree(temporary)

    def commit(self, paths: Optional[Sequence[str]] = None) -> subprocess.CompletedProcess:
        """Commit all effects, or only exact ledger-selected paths, to the host."""
        self._stop_worker()
        assert self.sandbox_dir is not None
        if paths is not None and not paths:
            return subprocess.CompletedProcess([], 0, "", "")
        metadata = self._capture_commit_metadata(paths)
        cmd = [str(self.try_bin)]
        if paths is not None:
            for pattern in self._include_patterns(paths):
                cmd.extend(["-I", pattern])
        cmd.extend(["commit", str(self.sandbox_dir)])
        upper_modes: Dict[Path, int] = {}
        upper = self.sandbox_dir / "upperdir"
        if upper.exists():
            _grant_upper_commit_access(upper, upper_modes)
        try:
            cp = subprocess.run(
                cmd,
                cwd=str(self.workspace),
                text=True,
                input="y\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        finally:
            _restore_upper_modes(upper_modes)
        commit_output = (cp.stdout or "") + "\n" + (cp.stderr or "")
        if cp.returncode == 0 and "couldn't commit" in commit_output:
            cp = subprocess.CompletedProcess(
                cp.args,
                1,
                cp.stdout,
                cp.stderr,
            )
        if cp.returncode == 0:
            self._restore_committed_metadata(metadata)
            self._cached_summary = {}
            self._cached_digests = self.upperdir_digests()
            self._pending_snapshot_changes = None
        return cp

    def reset(self) -> None:
        """Hard reset overlay (legacy). Prefer rollback_steps for surgical restore."""
        self._stop_worker()
        assert self.sandbox_dir is not None
        subprocess.run(["chmod", "-R", "u+rwX", str(self.sandbox_dir)], check=False)
        for child in list(self.sandbox_dir.iterdir()):
            if child.name in ("agenttx.json", "layers"):
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except FileNotFoundError:
                    pass
        self._cached_summary = {}
        self._cached_digests = {}
        self._pending_snapshot_changes = None

    def rollback_causal(
        self, step_ids: List[int], paths: Sequence[str]
    ) -> None:
        """Restore only causal write/delete paths and retain independent steps."""
        self._stop_worker()
        assert self.sandbox_dir is not None and self.layers is not None
        if not step_ids:
            return
        self.layers.restore_paths(min(step_ids), self.sandbox_dir / "upperdir", list(paths))
        self.layers.drop_from(step_ids)
        self._cached_summary = {}
        self._cached_digests = self.upperdir_digests()
        self._pending_snapshot_changes = None

    def rollback_steps(self, step_ids: List[int]) -> None:
        """Restore upperdir to snapshot taken before min(step_ids)."""
        self._stop_worker()
        assert self.sandbox_dir is not None and self.layers is not None
        if not step_ids:
            return
        first = min(step_ids)
        upper = self.sandbox_dir / "upperdir"
        self.layers.restore_before(first, upper)
        self.layers.drop_from(step_ids)
        self._cached_summary = {}
        self._cached_digests = self.upperdir_digests()
        self._pending_snapshot_changes = None

    def close(self, destroy: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        self._stop_worker()
        if not destroy and self.layers is not None:
            # Blob reachability is unchanged during normal execution; defer
            # this directory scan until the session is explicitly retained.
            self.layers.gc_blobs()
        if self._cmd_script is not None:
            shutil.rmtree(self._cmd_script.parent, ignore_errors=True)
            self._cmd_script = None
        if destroy and self._owns_sandbox and self.sandbox_dir is not None:
            subprocess.run(["chmod", "-R", "u+rwX", str(self.sandbox_dir)], check=False)
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)
            self.sandbox_dir = None

    def __enter__(self) -> "SharedSemisolate":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()
