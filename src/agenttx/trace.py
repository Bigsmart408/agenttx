"""Parse workspace-local read and negative-lookup effects from strace logs."""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Dict, List, Optional

from .ledger import Effect, EffectKind

_LINE_RE = re.compile(r"^\s*(?P<pid>\d+)\s+(?P<body>.*)$")
_CALL_RE = re.compile(
    r"^(?P<call>[A-Za-z0-9_]+)\((?P<args>.*)\)\s+=\s+(?P<result>.*)$"
)
_FD_PATH_RE = re.compile(r"^-?\d+<(?P<path>.*)>")
_NEGATIVE_RE = re.compile(r"^-\d+\s+(?:ENOENT|ENOTDIR)\b")

_FIRST_PATH_READS = {
    "access",
    "chdir",
    "execve",
    "lgetxattr",
    "listxattr",
    "llistxattr",
    "lstat",
    "readlink",
    "stat",
    "statfs",
}
_AT_PATH_READS = {
    "execveat",
    "faccessat",
    "faccessat2",
    "newfstatat",
    "readlinkat",
    "statx",
}


def _split_args(text: str) -> List[str]:
    """Split a syscall argument list while preserving quoted/nested commas."""
    args: List[str] = []
    start = 0
    depth = 0
    quoted = False
    escaped = False
    for index, char in enumerate(text):
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char in "([{":
            depth += 1
        elif char in ")]}":
            depth = max(0, depth - 1)
        elif char == "," and depth == 0:
            args.append(text[start:index].strip())
            start = index + 1
    args.append(text[start:].strip())
    return args


def _decode_string(token: str) -> Optional[str]:
    token = token.strip()
    if not token.startswith('"'):
        return None
    end = 1
    escaped = False
    while end < len(token):
        char = token[end]
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == '"':
            break
        end += 1
    if end >= len(token):
        return None
    try:
        value = ast.literal_eval(token[: end + 1])
    except (SyntaxError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _normal_path(path: Path) -> Path:
    return Path(os.path.normpath(str(path)))


def _resolve_path(token: str, base: Path) -> Optional[Path]:
    decoded = _decode_string(token)
    if decoded is None or not decoded:
        return None
    path = Path(decoded)
    return _normal_path(path if path.is_absolute() else base / path)


def _fd_base(token: str, fallback: Path) -> Path:
    if token.strip() == "AT_FDCWD":
        return fallback
    match = re.search(r"<(?P<path>/.*)>", token)
    if match:
        return _normal_path(Path(match.group("path")))
    return fallback


def _returned_path(result: str) -> Optional[Path]:
    match = _FD_PATH_RE.match(result.strip())
    if not match:
        return None
    path = match.group("path")
    if path.endswith(" (deleted)"):
        path = path[: -len(" (deleted)")]
    return _normal_path(Path(path)) if path.startswith("/") else None


def _workspace_path(path: Optional[Path], workspace: Path) -> Optional[str]:
    if path is None or path == workspace:
        return None
    try:
        path.relative_to(workspace)
    except ValueError:
        return None
    return str(path)


def _open_is_read(flags: str) -> bool:
    return "O_WRONLY" not in flags or "O_RDWR" in flags


def parse_strace_effects(text: str, workspace: Path) -> List[Effect]:
    """Return deduplicated workspace reads and failed path lookups.

    Successful writes are intentionally ignored here: the overlay fingerprint
    remains the source of truth for writes and deletes.
    """
    workspace = Path(workspace).resolve()
    cwd_by_pid: Dict[int, Path] = {}
    unfinished: Dict[int, str] = {}
    effects = set()

    for raw_line in text.splitlines():
        line_match = _LINE_RE.match(raw_line)
        if not line_match:
            continue
        pid = int(line_match.group("pid"))
        body = line_match.group("body")
        cwd = cwd_by_pid.setdefault(pid, workspace)

        if "<unfinished ...>" in body:
            unfinished[pid] = body.split("<unfinished ...>", 1)[0]
            continue
        if "<... " in body and " resumed>" in body:
            prefix = unfinished.pop(pid, "")
            body = prefix + body.split(" resumed>", 1)[1]
        call_match = _CALL_RE.match(body)
        if not call_match:
            continue

        call = call_match.group("call")
        args = _split_args(call_match.group("args"))
        result = call_match.group("result").strip()

        if call in {"clone", "clone3", "fork", "vfork"}:
            child_match = re.match(r"(?P<pid>\d+)\b", result)
            if child_match:
                cwd_by_pid[int(child_match.group("pid"))] = cwd
            continue

        path: Optional[Path] = None
        path_aliases: List[Path] = []
        flags = ""
        if call in {"open", "openat", "openat2"}:
            if call == "open":
                if len(args) < 2:
                    continue
                path = _resolve_path(args[0], cwd)
                flags = args[1]
            else:
                if len(args) < 3:
                    continue
                base = _fd_base(args[0], cwd)
                path = _resolve_path(args[1], base)
                flags = args[2]
            if not _NEGATIVE_RE.match(result) and _open_is_read(flags):
                requested = path
                returned = _returned_path(result)
                if returned is not None and requested is not None and returned != requested:
                    path_aliases.append(requested)
                path = returned or path
            elif not _NEGATIVE_RE.match(result):
                continue
        elif call in _FIRST_PATH_READS:
            if not args:
                continue
            path = _resolve_path(args[0], cwd)
        elif call in _AT_PATH_READS:
            if len(args) < 2:
                continue
            path = _resolve_path(args[1], _fd_base(args[0], cwd))
        else:
            continue

        kind = (
            EffectKind.NEGATIVE
            if _NEGATIVE_RE.match(result)
            else EffectKind.READ
        )
        for candidate in [path, *path_aliases]:
            effect_path = _workspace_path(candidate, workspace)
            if effect_path is not None:
                effects.add(Effect(effect_path, kind))

        if call == "chdir" and not result.startswith("-") and path is not None:
            cwd_by_pid[pid] = path

    return sorted(effects, key=lambda effect: (effect.path, effect.kind.value))
