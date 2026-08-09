"""Shared/incremental semisolate pool backed by binpash/try -N DIR."""
from __future__ import annotations
import base64, hashlib, json, os, re, shlex, shutil, stat, struct, subprocess, tempfile, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence
from .effects import SummaryEntry, diff_summaries, parse_try_summary
from .ledger import Effect, EffectKind
from .layers import LayerStore, _remove_overlay_tree
from .trace import parse_strace_effects

_WHITEOUT_DIGEST = "<agenttx:whiteout>"


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

@dataclass
class SharedSemisolate:
    workspace: Path
    try_bin: Path = field(default_factory=_default_try_bin)
    sandbox_dir: Optional[Path] = None
    hide_network: bool = False
    trace_reads: bool = True
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
    layers: Optional[LayerStore] = None

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        self.try_bin = Path(self.try_bin)
        if self.trace_reads and shutil.which("strace") is None:
            raise RuntimeError(
                "automatic dependency tracing requires strace; "
                "construct SharedSemisolate(trace_reads=False) to opt out"
            )
        if self.sandbox_dir is None:
            self.sandbox_dir = Path(tempfile.mkdtemp(prefix="agenttx-sandbox-", dir="/tmp"))
            self._owns_sandbox = True
        if self.layers is None:
            self.layers = LayerStore(self.sandbox_dir / "layers")
        else:
            self.sandbox_dir = Path(self.sandbox_dir)
            self.sandbox_dir.mkdir(parents=True, exist_ok=True)
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
                # overlay. Ignore crash leftovers and never expose them as effects.
                if (
                    rel.parent == Path("tmp")
                    and entry.name.startswith(".agenttx-strace-")
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
        self._ensure_worker()
        assert self._worker_process is not None
        assert self._worker_process.stdin is not None
        request = json.dumps(
            {"argv": list(command), "cwd": str(cwd)},
            separators=(",", ":"),
        ).encode("utf-8")
        if self._worker_crash_once:
            self._worker_crash_once = False
            self._worker_process.kill()
            self._worker_process.wait(timeout=2)
            raise RuntimeError("injected persistent-worker crash")
        self._worker_process.stdin.write(struct.pack("!I", len(request)))
        self._worker_process.stdin.write(request)
        self._worker_process.stdin.flush()
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
            list(command),
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
        if should_trace_reads:
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

        t0 = time.perf_counter()
        if self.persistent_worker:
            try:
                cp = self._run_worker(command, self.workspace)
            except Exception:
                # A worker failure must not change correctness. Restarting the
                # worker is deferred; this step uses the original try path.
                self._worker_failure_count += 1
                self._stop_worker()
                self._repair_worker_sandbox()
                cp = self._run_try([*flags, "--", *command], cwd=self.workspace)
        else:
            cp = self._run_try([*flags, "--", *command], cwd=self.workspace)
        duration = time.perf_counter() - t0

        trace_effects: List[Effect] = []
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
        return StepResult(step_index=idx, returncode=cp.returncode, stdout=cp.stdout, stderr=cp.stderr, summary_before=before, summary_after=after, duration_s=duration, effects=effects)

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
