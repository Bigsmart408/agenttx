"""Shared/incremental semisolate pool backed by binpash/try -N DIR."""
from __future__ import annotations
import hashlib, os, re, shlex, shutil, stat, subprocess, tempfile, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence
from .effects import SummaryEntry, diff_summaries, parse_try_summary
from .ledger import Effect, EffectKind
from .layers import LayerStore

_WHITEOUT_DIGEST = "<agenttx:whiteout>"


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
    _owns_sandbox: bool = False
    _step_count: int = 0
    _closed: bool = False
    _cached_summary: Dict[str, SummaryEntry] = field(default_factory=dict)
    _cached_digests: Dict[str, str] = field(default_factory=dict)
    layers: Optional[LayerStore] = None

    def __post_init__(self) -> None:
        self.workspace = Path(self.workspace).resolve()
        self.try_bin = Path(self.try_bin)
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
        return subprocess.run([str(self.try_bin), *args], cwd=str(cwd or self.workspace), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)

    def refresh_summary(self) -> Dict[str, SummaryEntry]:
        assert self.sandbox_dir is not None
        try:
            if not any(self.sandbox_dir.iterdir()):
                self._cached_summary = {}
                return self._cached_summary
        except FileNotFoundError:
            self._cached_summary = {}
            return self._cached_summary
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
        for entry in upper.rglob("*"):
            try:
                entry_stat = entry.lstat()
                mode = entry_stat.st_mode
                rel = entry.relative_to(upper)
                logical = Path("/" + rel.as_posix())

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
                    payload = b"file\0" + metadata + b"\0" + entry.read_bytes()
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
        cmd_dir = Path(tempfile.mkdtemp(prefix="agenttx-cmd-", dir="/tmp"))
        script = cmd_dir / "cmd.sh"
        if len(argv) >= 3 and Path(argv[0]).name == "bash" and argv[1] == "-c":
            body = "#!/usr/bin/env bash\nset -e\n" + argv[2] + "\n"
        else:
            body = "#!/usr/bin/env bash\nset -e\n" + " ".join(shlex.quote(a) for a in argv) + "\n"
        script.write_text(body, encoding="utf-8")
        script.chmod(0o755)
        return script

    def run(self, argv: Sequence[str]) -> StepResult:
        if self._closed:
            raise RuntimeError("SharedSemisolate is closed")
        assert self.sandbox_dir is not None
        before = dict(self._cached_summary)
        dig_before = dict(self._cached_digests)
        assert self.layers is not None and self.sandbox_dir is not None
        upper = self.sandbox_dir / "upperdir"
        self.layers.snapshot_before(self._step_count, upper)
        flags = ["-N", str(self.sandbox_dir)]
        if self.hide_network:
            flags.insert(0, "-x")
        script = self._write_cmd_script(argv)
        t0 = time.perf_counter()
        try:
            cp = self._run_try([*flags, "--", "bash", str(script)], cwd=self.workspace)
        finally:
            shutil.rmtree(script.parent, ignore_errors=True)
        duration = time.perf_counter() - t0
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
        effects = [effects_by_path[path] for path in sorted(effects_by_path)]
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

    def commit(self, paths: Optional[Sequence[str]] = None) -> subprocess.CompletedProcess:
        """Commit all effects, or only exact ledger-selected paths, to the host."""
        assert self.sandbox_dir is not None
        if paths is not None and not paths:
            return subprocess.CompletedProcess([], 0, "", "")
        cmd = [str(self.try_bin)]
        if paths is not None:
            for pattern in self._include_patterns(paths):
                cmd.extend(["-I", pattern])
        cmd.extend(["commit", str(self.sandbox_dir)])
        cp = subprocess.run(
            cmd,
            cwd=str(self.workspace),
            text=True,
            input="y\n",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if cp.returncode == 0:
            self._cached_summary = {}
            self._cached_digests = self.upperdir_digests()
        return cp

    def reset(self) -> None:
        """Hard reset overlay (legacy). Prefer rollback_steps for surgical restore."""
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

    def rollback_steps(self, step_ids: List[int]) -> None:
        """Restore upperdir to snapshot taken before min(step_ids)."""
        assert self.sandbox_dir is not None and self.layers is not None
        if not step_ids:
            return
        first = min(step_ids)
        upper = self.sandbox_dir / "upperdir"
        self.layers.restore_before(first, upper)
        self.layers.drop_from(step_ids)
        self._cached_summary = {}
        self._cached_digests = self.upperdir_digests()

    def close(self, destroy: bool = True) -> None:
        if self._closed:
            return
        self._closed = True
        if destroy and self._owns_sandbox and self.sandbox_dir is not None:
            subprocess.run(["chmod", "-R", "u+rwX", str(self.sandbox_dir)], check=False)
            shutil.rmtree(self.sandbox_dir, ignore_errors=True)
            self.sandbox_dir = None

    def __enter__(self) -> "SharedSemisolate":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()