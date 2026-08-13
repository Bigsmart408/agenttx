"""eBPF-backed dependency tracing: bpftrace script generation and effect parsing.

This module is the kernel-side replacement for the strace backend in
:mod:`agenttx.trace`.  It captures the same workspace-local read and
negative-lookup effects from Linux syscall tracepoints instead of ptrace:

* `ATXBPF_E` lines carry syscall entry data (pid, tid, call, dfd, flags,
  requested path) with the path as the final, rest-of-line field so that
  paths containing spaces survive the plain-`%s` output of bpftrace <= 0.9
  (no `%r` hex specifier exists before bpftrace 0.10).
* `ATXBPF_X` lines carry the syscall return value (negative errno on
  failure).  The userspace parser pairs entry/exit events per tid, which is
  exact because a thread executes one syscall at a time.
* `ATXBPF_R` lines carry the kernel-resolved path for in-flight opens
  (emitted from a `kprobe:vfs_open` section that is only generated when the
  installed bpftrace supports the `dpath()` builtin, i.e. >= 0.10).  This
  restores the symlink-alias granularity that strace gets from `-yy`.
* `ATXBPF_READY <seed>` marks the moment all probes are attached; the
  runtime blocks the traced command on a FIFO until it appears, so no
  syscall of the command can escape the trace.

The traced process tree is seeded in-kernel: `@allowed[seed]` is set at
BEGIN, and every successful clone/fork/vfork of an allowed process extends
the set (strace `-f` equivalence).

Kernel requirements: bpftrace with tracepoint support (any distro build) and
root (bpf syscall).  The availability probe fails closed, exactly like the
strace backend.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .ledger import Effect, EffectKind

ATX_MARKER = "ATXBPF"
READY_LINE = "ATXBPF_READY"
OK_LINE = "ATXBPF_OK"

# Process-shape syscalls: successful returns carry the child pid.
_PROCESS_CALLS = frozenset({"clone", "clone3", "fork", "vfork"})
# Open family: path is the second argument (after dfd), flags decide read vs write.
_OPEN_CALLS = frozenset({"open", "openat", "openat2"})
# Reads whose first argument is the path (no dirfd).
_PATH_READS = frozenset(
    {
        "access",
        "chdir",
        "execve",
        "getxattr",
        "lgetxattr",
        "listxattr",
        "llistxattr",
        "lstat",
        "readlink",
        "stat",
        "statfs",
    }
)
# Reads whose first argument is a dirfd (AT_FDCWD = -100).
_AT_PATH_READS = frozenset(
    {
        "execveat",
        "faccessat",
        "faccessat2",
        "newfstatat",
        "readlinkat",
        "statx",
    }
)
# Tracepoint field holding the path in each sys_enter_* format file.
_ENTER_PATH_FIELD = {
    "stat": "filename",
    "lstat": "filename",
    "access": "filename",
    "chdir": "filename",
    "execve": "filename",
    "readlink": "path",
    "readlinkat": "path",
    "statfs": "path",
    "getxattr": "path",
    "lgetxattr": "path",
    "listxattr": "path",
    "llistxattr": "path",
}
# All calls the tracer understands (process shape + reads + opens).
_SUPPORTED_CALLS = _PROCESS_CALLS | _PATH_READS | _AT_PATH_READS | _OPEN_CALLS

_AT_FDCWD = -100
_O_ACCMODE = 0o3
_O_WRONLY = 0o1
# errno values reported as negative tracepoint returns.
_ENOENT = 2
_ENOTDIR = 20

_BPFTRACE_MIN_RESOLVED = (0, 10)  # dpath() builtin


# ---------------------------------------------------------------------------
# Environment probes
# ---------------------------------------------------------------------------

def bpftrace_binary() -> Optional[str]:
    return shutil.which("bpftrace")


def bpftrace_version() -> Optional[Tuple[int, ...]]:
    """Return the installed bpftrace version as a tuple, or None."""
    binary = bpftrace_binary()
    if binary is None:
        return None
    try:
        cp = subprocess.run(
            [binary, "--version"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", cp.stdout or cp.stderr)
    if not match:
        return None
    return tuple(int(part) for part in match.groups() if part is not None)


def bpftrace_supports_resolved_paths() -> bool:
    version = bpftrace_version()
    return version is not None and version[:2] >= _BPFTRACE_MIN_RESOLVED


def available_syscall_tracepoints() -> Optional[set]:
    """Discover syscall tracepoints from tracefs; None when unreadable.

    Returns a set of names like ``sys_enter_openat`` / ``sys_exit_openat``.
    """
    for base in (
        "/sys/kernel/tracing/events/syscalls",
        "/sys/kernel/debug/tracing/events/syscalls",
    ):
        try:
            names = {
                name
                for name in os.listdir(base)
                if name.startswith(("sys_enter_", "sys_exit_"))
            }
        except OSError:
            continue
        if names:
            return names
    return None


def _tracepoint_name(call: str, direction: str) -> str:
    return f"sys_{direction}_{call}"


# Static default probe list for kernels where tracefs is not readable.  The
# attach pre-check retries with this list when discovery is impossible, so a
# wrong guess degrades to "BPF unavailable" instead of a broken trace.
_STATIC_ENTER_TRACEPOINTS = tuple(
    _tracepoint_name(call, "enter") for call in sorted(_SUPPORTED_CALLS)
)
_STATIC_EXIT_TRACEPOINTS = tuple(
    _tracepoint_name(call, "exit") for call in sorted(_SUPPORTED_CALLS)
)


def select_tracepoints(
    available: Optional[Iterable[str]],
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Return (enter, exit) tracepoint lists, filtering by availability."""
    wanted_enter = list(_STATIC_ENTER_TRACEPOINTS)
    wanted_exit = list(_STATIC_EXIT_TRACEPOINTS)
    if available is None:
        return tuple(wanted_enter), tuple(wanted_exit)
    known = set(available)
    enter = tuple(name for name in wanted_enter if name in known)
    exit_ = tuple(name for name in wanted_exit if name in known)
    return enter, exit_


# ---------------------------------------------------------------------------
# bpftrace script generation
# ---------------------------------------------------------------------------

def _probe_body(call: str) -> str:
    """Entry-probe body.  Process calls record nothing on entry (their retval
    and child pid are only known at exit), so they return an empty body."""
    if call in _PROCESS_CALLS:
        return ""
    path_field = _ENTER_PATH_FIELD.get(call, "filename")
    if call in _OPEN_CALLS:
        if call == "openat2":
            # open_how.flags is the first u64 of the user struct; dereferencing
            # through the tracepoint pointer avoids depending on BTF structs.
            flags_arg = "*(uint64*)args->how"
        else:
            flags_arg = "(uint64)args->flags"
        if call == "open":
            dfd_arg = "0"
        else:
            dfd_arg = "(int32)args->dfd"
        return (
            f'    printf("ATXBPF E %d %d {call} %d %llu %s\\n", '
            f"pid, tid, {dfd_arg}, {flags_arg}, str(args->{path_field}));\n"
        )
    if call in _AT_PATH_READS:
        return (
            f'    printf("ATXBPF E %d %d {call} %d 0 %s\\n", '
            f"pid, tid, (int32)args->dfd, str(args->{path_field}));\n"
        )
    # plain path reads
    return (
        f'    printf("ATXBPF E %d %d {call} 0 0 %s\\n", '
        f"pid, tid, str(args->{path_field}));\n"
    )


def _exit_body(call: str) -> str:
    if call in _PROCESS_CALLS:
        # Extend the allowed process tree in-kernel and report the child pid
        # so the userspace parser can propagate per-process cwd state.
        return (
            "    if ((int64)args->ret > 0) {\n"
            "        @allowed[(uint64)args->ret] = 1;\n"
            "    }\n"
            f'    printf("ATXBPF X %d %d {call} %lld\\n", '
            "pid, tid, (int64)args->ret);\n"
        )
    return (
        f'    printf("ATXBPF X %d %d {call} %lld\\n", '
        "pid, tid, (int64)args->ret);\n"
    )


def build_bpftrace_script(
    available: Optional[Iterable[str]] = None,
    *,
    with_resolved: bool = False,
) -> str:
    """Generate the bpftrace dependency-tracing script.

    `available` optionally restricts the syscall tracepoint set (names like
    ``sys_enter_openat``); None uses the static default list.  `with_resolved`
    adds the kprobe:vfs_open section (requires bpftrace >= 0.10 with the
    ``dpath()`` builtin) that reports kernel-resolved open paths.
    """
    enter, exit_ = select_tracepoints(available)
    lines = [
        "/* AgentTX dependency tracer (generated). */",
        "BEGIN",
        "{",
        "    @allowed[(uint64)$1] = 1;",
        f'    printf("{READY_LINE} %d\\n", $1);',
        "}",
        "",
    ]
    if enter:
        lines.append(",\n".join(f"tracepoint:{name}" for name in enter))
        lines.append("/@allowed[(uint64)pid]/")
        lines.append("{")
        for call in sorted(_SUPPORTED_CALLS):
            enter_name = _tracepoint_name(call, "enter")
            if enter_name in enter:
                body = _probe_body(call)
                if body:
                    lines.append(body)
        lines.append("}")
        lines.append("")
    if exit_:
        lines.append(",\n".join(f"tracepoint:{name}" for name in exit_))
        lines.append("/@allowed[(uint64)pid]/")
        lines.append("{")
        for call in sorted(_SUPPORTED_CALLS):
            exit_name = _tracepoint_name(call, "exit")
            if exit_name in exit_:
                lines.append(_exit_body(call))
        lines.append("}")
        lines.append("")
    if with_resolved:
        lines.extend(
            [
                "kprobe:vfs_open",
                "/@allowed[(uint64)pid]/",
                "{",
                "    printf(\"ATXBPF R %d %d %s\\n\", pid, tid, "
                "dpath((struct path *)arg0));",
                "}",
                "",
            ]
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Attach pre-check
# ---------------------------------------------------------------------------

def bpf_attach_precheck(
    script: str,
    *,
    timeout: float = 40.0,
    extra_env: Optional[Dict[str, str]] = None,
) -> Tuple[bool, str]:
    """Compile and attach the full script once, without a real command.

    Runs bpftrace with ``-c /bin/true`` so every probe must attach before the
    one-shot command exits.  Returns (ok, detail).  This is the authoritative
    availability check: a script whose probes cannot attach fails here rather
    than mid-step.
    """
    binary = bpftrace_binary()
    if binary is None:
        return False, "bpftrace binary not found"
    env = {
        **os.environ,
        "BPFTRACE_STRLEN": "4096",
        **(extra_env or {}),
    }
    try:
        cp = subprocess.run(
            [binary, "-q", "-c", "/bin/true", "-e", script],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return False, "bpftrace attach pre-check timed out"
    except OSError as exc:
        return False, f"{type(exc).__name__}: {exc}"
    if cp.returncode != 0:
        detail = (cp.stderr or cp.stdout).strip().splitlines()
        return False, detail[-1] if detail else f"rc={cp.returncode}"
    if OK_LINE not in cp.stdout:
        return False, "bpftrace pre-check produced no readiness marker"
    return True, "ok"


def bpf_static_available() -> Tuple[bool, str]:
    """Cheap, non-attaching availability test (root + binary)."""
    if os.geteuid() != 0:
        return False, "eBPF tracing requires root (euid != 0)"
    binary = bpftrace_binary()
    if binary is None:
        return False, "bpftrace binary not found"
    version = bpftrace_version()
    version_text = f" (v{'.'.join(map(str, version))})" if version else ""
    return True, f"bpftrace{version_text}"


def resolve_bpf_script() -> Tuple[str, bool, str]:
    """Build the script for this host; returns (script, resolved_paths, detail).

    Tries the full probe set, then falls back to a reduced set without the
    resolved-path kprobe when the installed bpftrace predates ``dpath()`` or
    the kprobe cannot attach.  Never returns an unattachable script.
    """
    static_ok, static_detail = bpf_static_available()
    if not static_ok:
        return "", False, static_detail
    discovered = available_syscall_tracepoints()
    if discovered is not None:
        script = build_bpftrace_script(discovered)
        ok, detail = bpf_attach_precheck(script)
        if ok:
            return script, False, "tracepoints"
        # Discovery may be stale or the kernel may reject a specific probe;
        # fall through to the legacy-retry path below.
    script = build_bpftrace_script(None)
    ok, detail = bpf_attach_precheck(script)
    if ok:
        return script, False, "default tracepoints"
    with_resolved = bpftrace_supports_resolved_paths()
    if with_resolved:
        script = build_bpftrace_script(None, with_resolved=True)
        ok, detail = bpf_attach_precheck(script)
        if ok:
            return script, True, "default tracepoints + resolved paths"
    return "", False, detail or "no attachable probe set"


# ---------------------------------------------------------------------------
# ATXBPF line parsing
# ---------------------------------------------------------------------------

def _entry_fields(line: str) -> Optional[Tuple[int, int, str, int, int, str]]:
    """Parse one entry line; returns (pid, tid, call, dfd, flags, path).

    The path is the final rest-of-line field and may be empty for process
    calls (clone/fork/vfork/clone3 carry no path argument).  Malformed lines
    return None and are dropped by the caller.
    """
    parts = line.split(None, 5)
    if len(parts) < 6 or parts[1] != "E":
        return None
    try:
        tokens = parts[5].split(None, 2)
        dfd = int(tokens[0])
        flags = int(tokens[1]) if len(tokens) > 1 else 0
        path_text = tokens[2] if len(tokens) > 2 else ""
        return (
            int(parts[2]),
            int(parts[3]),
            parts[4],
            dfd,
            flags,
            path_text,
        )
    except ValueError:
        return None


def _exit_fields(line: str) -> Optional[Tuple[int, int, str, int]]:
    parts = line.split()
    if len(parts) != 6 or parts[1] != "X":
        return None
    try:
        return int(parts[2]), int(parts[3]), parts[4], int(parts[5])
    except ValueError:
        return None


def _resolved_fields(line: str) -> Optional[Tuple[int, int, str]]:
    parts = line.split(None, 4)
    if len(parts) != 5 or parts[1] != "R":
        return None
    try:
        return int(parts[2]), int(parts[3]), parts[4]
    except ValueError:
        return None


def _normal(path: Path) -> Path:
    return Path(os.path.normpath(str(path)))


def _add_workspace(
    candidates: Sequence[Path],
    kind: EffectKind,
    workspace: Path,
    effects: set,
) -> None:
    for candidate in candidates:
        if candidate == workspace:
            continue
        try:
            candidate.relative_to(workspace)
        except ValueError:
            continue
        effects.add(Effect(str(candidate), kind))


def _apply_exit(
    pid: int,
    tid: int,
    call: str,
    path: Path,
    flags: int,
    retval: int,
    cwd_by_pid: Dict[int, Path],
    resolved: Dict[int, Path],
    workspace: Path,
    effects: set,
) -> None:
    if call in _PROCESS_CALLS:
        if retval > 0:
            cwd_by_pid[retval] = cwd_by_pid.get(pid, workspace)
        return
    if call == "chdir":
        if retval == 0:
            cwd_by_pid[pid] = path
        return
    negative = retval < 0 and -retval in (_ENOENT, _ENOTDIR)
    if call in _OPEN_CALLS:
        if retval >= 0:
            if (flags & _O_ACCMODE) == _O_WRONLY:
                resolved.pop(tid, None)
                return
            candidates = [path]
            returned = resolved.pop(tid, None)
            if returned is not None and returned != path:
                candidates.append(returned)
            _add_workspace(candidates, EffectKind.READ, workspace, effects)
        elif negative:
            _add_workspace([path], EffectKind.NEGATIVE, workspace, effects)
        elif (flags & _O_ACCMODE) != _O_WRONLY:
            # Non-ENOENT open failure still proves a read attempt when the
            # flags are read-oriented (mirrors the strace parser).
            _add_workspace([path], EffectKind.READ, workspace, effects)
        return
    if call in _PATH_READS or call in _AT_PATH_READS:
        # Any failure except ENOENT/ENOTDIR is still a read attempt, exactly
        # like the strace parser's result classification.
        kind = EffectKind.NEGATIVE if negative else EffectKind.READ
        _add_workspace([path], kind, workspace, effects)


def parse_bpf_effects(text: str, workspace: Path) -> List[Effect]:
    """Return deduplicated workspace reads and failed path lookups.

    Mirrors :func:`agenttx.trace.parse_strace_effects` semantics:

    * successful read-flagged opens and read-family syscalls record READ;
    * ENOENT/ENOTDIR failures record NEGATIVE;
    * write-flagged opens are ignored (the overlay fingerprint owns writes);
    * chdir updates per-process cwd; clone/fork/vfork propagate it to the
      child pid;
    * when a resolved path (ATXBPF_R) differs from the requested path, both
      are recorded (symlink-alias granularity).
    """
    workspace = Path(workspace).resolve()
    cwd_by_pid: Dict[int, Path] = {}
    pending: Dict[int, Tuple[int, str, Path, int]] = {}
    resolved: Dict[int, Path] = {}
    effects = set()

    for raw_line in text.splitlines():
        if not raw_line.startswith(ATX_MARKER):
            continue
        entry = _entry_fields(raw_line)
        if entry is not None:
            pid, tid, call, dfd, flags, path_text = entry
            if call in _OPEN_CALLS:
                resolved.pop(tid, None)
            base = cwd_by_pid.get(pid, workspace)
            if path_text:
                # Non-AT_FDCWD dirfds are approximated against cwd until a
                # resolved-path probe (ATXBPF_R) supplies the true target.
                path = (
                    _normal(base / path_text)
                    if not path_text.startswith("/")
                    else _normal(Path(path_text))
                )
            else:
                # Process calls carry no path; the placeholder is ignored.
                path = workspace
            pending[tid] = (pid, call, path, flags)
            continue
        exit_ = _exit_fields(raw_line)
        if exit_ is not None:
            pid, tid, call, retval = exit_
            entry = pending.pop(tid, None)
            if entry is None:
                continue
            entry_pid, entry_call, path, flags = entry
            if entry_call != call:
                continue
            _apply_exit(
                pid,
                tid,
                call,
                path,
                flags,
                retval,
                cwd_by_pid,
                resolved,
                workspace,
                effects,
            )
            continue
        resolved_line = _resolved_fields(raw_line)
        if resolved_line is not None:
            pid, tid, path_text = resolved_line
            if path_text.startswith("/"):
                resolved[tid] = _normal(Path(path_text))

    return sorted(effects, key=lambda effect: (effect.path, effect.kind.value))


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------

def resolve_trace_backend(
    trace_backend: str,
    *,
    strace_present: bool,
    bpf: Optional[Tuple[bool, str]] = None,
) -> Tuple[str, str]:
    """Choose the tracing backend for one step.

    `trace_backend` is ``auto``, ``strace`` or ``bpf``.  ``auto`` prefers the
    eBPF backend when the attach pre-check succeeded, otherwise strace; it
    fails closed when neither is available.  Returns (backend, detail).
    """
    if trace_backend == "strace":
        if not strace_present:
            raise RuntimeError(
                "trace backend 'strace' requested but strace is unavailable"
            )
        return "strace", "strace"
    if trace_backend == "bpf":
        if bpf is None or not bpf[0]:
            detail = bpf[1] if bpf is not None else "not probed"
            raise RuntimeError(
                f"trace backend 'bpf' requested but eBPF tracing is "
                f"unavailable: {detail}"
            )
        return "bpf", bpf[1]
    # auto
    if bpf is not None and bpf[0]:
        return "bpf", bpf[1]
    if strace_present:
        return "strace", "strace (eBPF unavailable)"
    raise RuntimeError(
        "automatic dependency tracing requires strace or a working eBPF "
        "tracer; construct SharedSemisolate(trace_reads=False) to opt out"
    )


def wait_for_bpftrace_ready(
    log_path: Path,
    seed: int,
    *,
    timeout: float = 20.0,
    abort_check: Optional[Callable[[], bool]] = None,
) -> Tuple[bool, float]:
    """Poll the bpftrace log for the READY marker of `seed`.

    Returns (ready, elapsed_seconds).  On timeout the caller must release any
    blocked command and fail closed.  `abort_check` (e.g. the bpftrace process
    having exited) short-circuits the wait.
    """
    wanted = f"{READY_LINE} {seed}"
    start = time.monotonic()
    deadline = start + timeout
    while True:
        try:
            if log_path.exists():
                with open(log_path, "r", encoding="utf-8", errors="replace") as handle:
                    for line in handle:
                        if line.startswith(wanted):
                            return True, time.monotonic() - start
        except OSError:
            pass
        if abort_check is not None and abort_check():
            return False, time.monotonic() - start
        if time.monotonic() >= deadline:
            return False, timeout
        time.sleep(0.02)
