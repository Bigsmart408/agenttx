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
  runtime blocks the traced command on a release marker whose content it
  polls until the marker flips to the "go" value, so no syscall of the
  command can escape the trace.

The traced process tree is filtered in userspace: syscall tracepoints are
global, so every relevant syscall on the host is emitted and
:func:`parse_bpf_effects` keeps only events whose pid belongs to the seed's
descendant tree.  The runtime snapshots the tree from ``/proc`` when the
READY marker appears (the try sandbox's setup forks happen *before* the
probes attach, so in-kernel fork tracking can never see them), and the
parser extends the set with `ATXBPF F` lines from the sched_process_fork
tracepoint, whose pids are global (strace ``-f`` equivalence for forks
after attach; sys_exit_clone's retval is namespace-local and unusable when
the sandbox runs in its own pid namespace).

Kernel requirements: bpftrace with tracepoint support (any distro build) and
root (bpf syscall).  The availability probe fails closed, exactly like the
strace backend.
"""

from __future__ import annotations

import functools
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
# Tracepoint field holding the path in each sys_enter_* format file.  The
# argument-name fields ("pathname") are what modern kernels (>= 4.x) expose;
# _ENTER_PATH_FIELD_LEGACY carries the older "path" naming for the same calls
# so the attach pre-check can retry when a host exposes the old layout.
_ENTER_PATH_FIELD = {
    "stat": "filename",
    "lstat": "filename",
    "access": "filename",
    "chdir": "filename",
    "execve": "filename",
    "readlink": "path",
    "readlinkat": "pathname",
    "statfs": "pathname",
    "getxattr": "pathname",
    "lgetxattr": "pathname",
    "listxattr": "pathname",
    "llistxattr": "pathname",
}
_ENTER_PATH_FIELD_LEGACY = {
    "readlinkat": "path",
    "statfs": "path",
    "getxattr": "path",
    "lgetxattr": "path",
    "listxattr": "path",
    "llistxattr": "path",
}
# Tracepoint field holding the dirfd argument: execveat names it "fd".
_ENTER_DFD_FIELD = {"execveat": "fd"}
# All calls the tracer understands (process shape + reads + opens).
_SUPPORTED_CALLS = _PROCESS_CALLS | _PATH_READS | _AT_PATH_READS | _OPEN_CALLS

_AT_FDCWD = -100
_O_ACCMODE = 0o3
_O_WRONLY = 0o1
# errno values reported as negative tracepoint returns.
_ENOENT = 2
_ENOTDIR = 20

_BPFTRACE_MIN_RESOLVED = (0, 10)  # dpath() builtin
_BPFTRACE_MIN_QUIET = (0, 10)  # -q/--quiet flag
_BPFTRACE_MIN_LONG_STRINGS = (0, 10)  # scratch-map strings (> 200 bytes)


# ---------------------------------------------------------------------------
# Environment probes
# ---------------------------------------------------------------------------

def bpftrace_binary() -> Optional[str]:
    return shutil.which("bpftrace")


def bpftrace_version() -> Optional[Tuple[int, ...]]:
    """Return the installed bpftrace version as a tuple, or None.

    Cached: the version cannot change during a session, and the flag/strlen
    helpers below run on every eBPF-traced step, so an uncached probe would
    spawn one `bpftrace --version` subprocess per step.
    """
    return _bpftrace_version()


@functools.lru_cache(maxsize=1)
def _bpftrace_version() -> Optional[Tuple[int, ...]]:
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


def bpftrace_quiet_flag() -> List[str]:
    """Return the ``-q`` flag when the installed bpftrace supports it.

    bpftrace < 0.10 has no ``-q``/``--quiet`` option and exits with a usage
    error when passed; older versions simply print their attach/dump chatter
    to stdout, which the ATXBPF line parser ignores.
    """
    version = bpftrace_version()
    if version is None or version[:2] < _BPFTRACE_MIN_QUIET:
        return []
    return ["-q"]


def bpftrace_strlen_env() -> Dict[str, str]:
    """Return the BPFTRACE_STRLEN environment for this bpftrace.

    bpftrace >= 0.10 stores long strings in a scratch map and accepts 4096
    (paths up to 4095 bytes).  Older builds keep strings on the 512-byte BPF
    stack and hard-fail on any value above 200 bytes, so they get 200.
    """
    version = bpftrace_version()
    if version is None or version[:2] < _BPFTRACE_MIN_LONG_STRINGS:
        return {"BPFTRACE_STRLEN": "200"}
    return {"BPFTRACE_STRLEN": "4096"}


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


# Tracepoint-name aliases for syscalls whose tracepoint is named after the
# underlying kernel function instead of the syscall table entry.  Ubuntu 5.4
# exposes the `stat` syscall as sys_enter_newstat and `lstat` as
# sys_enter_newlstat; other kernels use sys_enter_stat/sys_enter_lstat.  The
# emitted event still carries the syscall name (stat/lstat), which is what
# the userspace parser matches.
_TRACEPOINT_ALIASES = {
    "stat": ("stat", "newstat"),
    "lstat": ("lstat", "newlstat"),
}


def _tracepoint_candidates(call: str, direction: str) -> Tuple[str, ...]:
    return tuple(
        f"sys_{direction}_{name}"
        for name in _TRACEPOINT_ALIASES.get(call, (call,))
    )


# Static default probe list for kernels where tracefs is not readable.  The
# attach pre-check retries with this list when discovery is impossible, so a
# wrong guess degrades to "BPF unavailable" instead of a broken trace.
_STATIC_ENTER_TRACEPOINTS = tuple(
    _tracepoint_candidates(call, "enter")[0] for call in sorted(_SUPPORTED_CALLS)
)
_STATIC_EXIT_TRACEPOINTS = tuple(
    _tracepoint_candidates(call, "exit")[0] for call in sorted(_SUPPORTED_CALLS)
)


def select_tracepoints(
    available: Optional[Iterable[str]],
) -> Tuple[Tuple[str, ...], Tuple[str, ...]]:
    """Return (enter, exit) tracepoint lists, filtering by availability.

    For aliased syscalls (stat -> newstat), the first candidate present in
    `available` is chosen, so the generated script attaches on kernels that
    name the tracepoint either way.
    """
    if available is None:
        return tuple(_STATIC_ENTER_TRACEPOINTS), tuple(_STATIC_EXIT_TRACEPOINTS)
    known = set(available)
    enter = []
    exit_ = []
    for call in sorted(_SUPPORTED_CALLS):
        for candidate in _tracepoint_candidates(call, "enter"):
            if candidate in known:
                enter.append(candidate)
                break
        for candidate in _tracepoint_candidates(call, "exit"):
            if candidate in known:
                exit_.append(candidate)
                break
    return tuple(enter), tuple(exit_)


# ---------------------------------------------------------------------------
# bpftrace script generation
# ---------------------------------------------------------------------------

def _probe_body(
    call: str,
    path_fields: Optional[Dict[str, str]] = None,
    dfd_fields: Optional[Dict[str, str]] = None,
) -> str:
    """Entry-probe body.  Process calls record nothing on entry (their retval
    and child pid are only known at exit), so they return an empty body."""
    path_field = (path_fields or _ENTER_PATH_FIELD).get(call, "filename")
    dfd_field = (dfd_fields or _ENTER_DFD_FIELD).get(call, "dfd")
    if call in _PROCESS_CALLS:
        return ""
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
            dfd_arg = f"(int32)args->{dfd_field}"
        return (
            f'    printf("ATXBPF E %d %d {call} %d %llu %s\\n", '
            f"pid, tid, {dfd_arg}, {flags_arg}, str(args->{path_field}));\n"
        )
    if call in _AT_PATH_READS:
        return (
            f'    printf("ATXBPF E %d %d {call} %d 0 %s\\n", '
            f"pid, tid, (int32)args->{dfd_field}, str(args->{path_field}));\n"
        )
    # plain path reads
    return (
        f'    printf("ATXBPF E %d %d {call} 0 0 %s\\n", '
        f"pid, tid, str(args->{path_field}));\n"
    )


def _exit_body(call: str) -> str:
    if call in _PROCESS_CALLS:
        # Report the child pid so the userspace parser can extend the traced
        # process tree and propagate per-process cwd state.
        return (
            f'    printf("ATXBPF X %d %d {call} %lld\\n", '
            "pid, tid, (int64)args->ret);\n"
        )
    return (
        f'    printf("ATXBPF X %d %d {call} %lld\\n", '
        "pid, tid, (int64)args->ret);\n"
    )


def _call_for_tracepoint(tracepoint_name: str) -> str:
    """Map an emitted tracepoint name back to the syscall name for events."""
    for prefix in ("sys_enter_", "sys_exit_"):
        if tracepoint_name.startswith(prefix):
            suffix = tracepoint_name[len(prefix):]
            break
    else:
        suffix = tracepoint_name
    for call, candidates in _TRACEPOINT_ALIASES.items():
        if suffix in candidates:
            return call
    return suffix


def build_bpftrace_script(
    available: Optional[Iterable[str]] = None,
    *,
    with_resolved: bool = False,
    path_fields: Optional[Dict[str, str]] = None,
    dfd_fields: Optional[Dict[str, str]] = None,
) -> str:
    """Generate the bpftrace dependency-tracing script.

    `available` optionally restricts the syscall tracepoint set (names like
    ``sys_enter_openat``); None uses the static default list.  `with_resolved`
    adds the kprobe:vfs_open section (requires bpftrace >= 0.10 with the
    ``dpath()`` builtin) that reports kernel-resolved open paths.
    `path_fields`/`dfd_fields` override per-call tracepoint argument names
    (used by the attach pre-check to retry legacy field layouts).

    Probes fire for every process on the host; the userspace parser filters
    events to the seed's descendant tree (see module docstring).
    """
    enter, exit_ = select_tracepoints(available)
    lines = [
        "/* AgentTX dependency tracer (generated). */",
        "BEGIN",
        "{",
        f'    printf("{READY_LINE} %d\\n", $1);',
        "}",
        "",
    ]
    # One probe block per syscall, never comma-joined: bpftrace resolves
    # `args` against the first probe of a joined block, and syscall
    # tracepoints have heterogeneous structs, so joined blocks only compile
    # when every probe shares the same field layout.  Separate blocks work on
    # every bpftrace version (0.9.x included).
    for name in enter:
        call = _call_for_tracepoint(name)
        body = _probe_body(call, path_fields, dfd_fields)
        if body:
            lines.extend(
                [
                    f"tracepoint:syscalls:{name}",
                    "{",
                    body,
                    "}",
                    "",
                ]
            )
    for name in exit_:
        call = _call_for_tracepoint(name)
        lines.extend(
            [
                f"tracepoint:syscalls:{name}",
                "{",
                _exit_body(call),
                "}",
                "",
            ]
        )
    # Process-tree extension: sched_process_fork reports the parent and child
    # pids from task_struct, which are the GLOBAL pids.  sys_exit_clone's ret
    # is namespace-local (the pid the syscall returns to its caller), so it
    # cannot be matched against the userspace /proc snapshot when the sandbox
    # runs in its own pid namespace (try does unshare --pid).
    lines.extend(
        [
            "tracepoint:sched:sched_process_fork",
            "{",
            '    printf("ATXBPF F %d %d\\n", '
            "args->parent_pid, args->child_pid);",
            "}",
            "",
        ]
    )
    if with_resolved:
        lines.extend(
            [
                "kprobe:vfs_open",
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
        **bpftrace_strlen_env(),
        **(extra_env or {}),
    }
    try:
        cp = subprocess.run(
            [*bpftrace_quiet_flag(), binary, "-c", "/bin/true", "-e", script, "1"],
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
    if f"{READY_LINE} 1" not in cp.stdout:
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

    Tries the full probe set, then falls back to reduced/legacy variants:
    the static probe list when tracefs discovery is unavailable or stale, a
    legacy tracepoint field layout (``path`` instead of ``pathname``) for
    hosts that expose it, and finally the resolved-path kprobe when the
    installed bpftrace supports ``dpath()``.  Never returns an unattachable
    script.
    """
    static_ok, static_detail = bpf_static_available()
    if not static_ok:
        return "", False, static_detail
    discovered = available_syscall_tracepoints()
    variants: List[Tuple[str, Optional[set], Optional[Dict[str, str]]]] = []
    if discovered is not None:
        variants.append(("tracepoints", discovered, None))
        variants.append(("tracepoints + legacy fields", discovered,
                         _ENTER_PATH_FIELD_LEGACY))
    variants.append(("default tracepoints", None, None))
    variants.append(("default tracepoints + legacy fields", None,
                     _ENTER_PATH_FIELD_LEGACY))
    last_detail = "no attachable probe set"
    for label, avail, path_fields in variants:
        script = build_bpftrace_script(avail, path_fields=path_fields)
        ok, detail = bpf_attach_precheck(script)
        if ok:
            return script, False, label
        last_detail = detail or last_detail
    if bpftrace_supports_resolved_paths():
        script = build_bpftrace_script(None, with_resolved=True)
        ok, detail = bpf_attach_precheck(script)
        if ok:
            return script, True, "default tracepoints + resolved paths"
        last_detail = detail or last_detail
    return "", False, last_detail


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


def _fork_fields(line: str) -> Optional[Tuple[int, int]]:
    """Parse one fork line; returns (parent_pid, child_pid).

    The sched_process_fork tracepoint reports global pids (from
    task_struct), unlike sys_exit_clone's ret, which is the pid the syscall
    returned in the caller's (possibly nested) pid namespace.
    """
    parts = line.split()
    if len(parts) != 4 or parts[1] != "F":
        return None
    try:
        return int(parts[2]), int(parts[3])
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


def parse_bpf_effects(
    text: str,
    workspace: Path,
    allowed_pids: Optional[Iterable[int]] = None,
) -> List[Effect]:
    """Return deduplicated workspace reads and failed path lookups.

    Mirrors :func:`agenttx.trace.parse_strace_effects` semantics:

    * successful read-flagged opens and read-family syscalls record READ;
    * ENOENT/ENOTDIR failures record NEGATIVE;
    * write-flagged opens are ignored (the overlay fingerprint owns writes);
    * chdir updates per-process cwd; clone/fork/vfork propagate it to the
      child pid;
    * when a resolved path (ATXBPF_R) differs from the requested path, both
      are recorded (symlink-alias granularity).

    `allowed_pids` optionally restricts parsing to the seed's traced process
    tree: events of other pids are dropped, and `ATXBPF F` fork lines
    (sched_process_fork, global pids) of an allowed parent extend the set and
    propagate the parent's cwd, so children forked after the READY snapshot
    are still traced (strace `-f` equivalence).  None (used by the unit
    tests) accepts every pid and keeps the clone/fork retval-based cwd
    propagation.
    """
    workspace = Path(workspace).resolve()
    cwd_by_pid: Dict[int, Path] = {}
    pending: Dict[int, Tuple[int, str, Path, int]] = {}
    resolved: Dict[int, Path] = {}
    effects = set()
    allowed: Optional[set] = set(allowed_pids) if allowed_pids is not None else None

    for raw_line in text.splitlines():
        if not raw_line.startswith(ATX_MARKER):
            continue
        entry = _entry_fields(raw_line)
        if entry is not None:
            pid, tid, call, dfd, flags, path_text = entry
            if allowed is not None and pid not in allowed:
                continue
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
            if allowed is not None and pid not in allowed:
                continue
            entry = pending.pop(tid, None)
            if entry is None:
                continue
            entry_pid, entry_call, path, flags = entry
            if entry_call != call:
                continue
            if allowed is not None and call in _PROCESS_CALLS:
                # The syscall retval is the child pid in the caller's pid
                # namespace, unusable against the global-pid /proc snapshot;
                # the sched_process_fork line already propagated the tree and
                # the child's cwd.
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
        fork = _fork_fields(raw_line)
        if fork is not None:
            parent, child = fork
            if allowed is None:
                # Unfiltered (unit-test) mode: nothing to extend.
                continue
            if parent not in allowed:
                continue
            allowed.add(child)
            # The child inherits the parent's cwd at fork time (the syscall
            # exit's retval is a namespace-local pid, unusable as a key here).
            cwd_by_pid.setdefault(child, cwd_by_pid.get(parent, workspace))
            continue
        resolved_line = _resolved_fields(raw_line)
        if resolved_line is not None:
            pid, tid, path_text = resolved_line
            if allowed is not None and pid not in allowed:
                continue
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
