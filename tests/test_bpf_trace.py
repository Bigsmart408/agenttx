"""Unit tests for the eBPF dependency tracer (parser, script gen, backend)."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

import agenttx.bpf_trace as bpf_trace
import agenttx.semisolate as semisolate_module
from agenttx.cli import build_parser
from agenttx.ledger import Effect, EffectKind
from agenttx.semisolate import SharedSemisolate

_O_RDONLY = 0o0
_O_WRONLY = 0o1
_O_RDWR = 0o2
_O_CREAT = 0o100
_O_TRUNC = 0o1000
_O_CLOEXEC = 0o2000000
_READ_FLAGS = _O_RDONLY | _O_CLOEXEC
_WRITE_FLAGS = _O_WRONLY | _O_CREAT | _O_TRUNC


def _entry(call: str, path: str, *, pid: int = 100, tid: int = 100,
           dfd: int = -100, flags: int = _READ_FLAGS) -> str:
    return f"ATXBPF E {pid} {tid} {call} {dfd} {flags} {path}"


def _exit(call: str, retval: int, *, pid: int = 100, tid: int = 100) -> str:
    return f"ATXBPF X {pid} {tid} {call} {retval}"


def test_parse_bpf_distinguishes_reads_negatives_and_writes(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    raw = "\n".join(
        [
            _entry("openat", str(workspace / "input.txt")),
            _exit("openat", 3),
            _entry("newfstatat", str(workspace / "missing.txt"), flags=0),
            _exit("newfstatat", -2),
            _entry("openat", str(workspace / "output.txt"), flags=_WRITE_FLAGS),
            _exit("openat", 4),
        ]
    )

    effects = set(bpf_trace.parse_bpf_effects(raw, workspace))

    assert Effect(str(workspace / "input.txt"), EffectKind.READ) in effects
    assert Effect(str(workspace / "missing.txt"), EffectKind.NEGATIVE) in effects
    assert Effect(str(workspace / "output.txt"), EffectKind.READ) not in effects


def test_parse_bpf_preserves_symlink_alias(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    raw = "\n".join(
        [
            _entry("openat", str(workspace / "alias" / "data.txt")),
            f"ATXBPF R 100 100 {workspace}/real/data.txt",
            _exit("openat", 3),
        ]
    )

    effects = set(bpf_trace.parse_bpf_effects(raw, workspace))

    assert Effect(str(workspace / "alias" / "data.txt"), EffectKind.READ) in effects
    assert Effect(str(workspace / "real" / "data.txt"), EffectKind.READ) in effects


def test_parse_bpf_tracks_chdir_across_child_processes(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    raw = "\n".join(
        [
            _entry("chdir", str(workspace / "sub"), flags=0),
            _exit("chdir", 0),
            _entry("clone", "", flags=0),
            _exit("clone", 101),
            _entry("newfstatat", str(workspace / "sub" / "missing.txt"),
                   pid=101, tid=101, flags=0),
            _exit("newfstatat", -20, pid=101, tid=101),
        ]
    )

    effects = bpf_trace.parse_bpf_effects(raw, workspace)

    assert Effect(
        str(workspace / "sub" / "missing.txt"), EffectKind.NEGATIVE
    ) in effects


def test_parse_bpf_handles_paths_with_spaces(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    raw = "\n".join(
        [
            _entry("stat", str(workspace / "a file with spaces.txt"), flags=0),
            _exit("stat", 0),
        ]
    )

    effects = bpf_trace.parse_bpf_effects(raw, workspace)

    assert Effect(str(workspace / "a file with spaces.txt"), EffectKind.READ) in effects


def test_parse_bpf_ignores_malformed_lines(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    raw = "\n".join(
        [
            "garbage line",
            "ATXBPF E 100",
            "ATXBPF X 100 100 openat abc",
            "ATXBPF R 100",
            "ATXBPF_READY 42",
            _entry("openat", str(workspace / "ok.txt")),
            _exit("openat", 3),
            "ATXBPF E 100 100 openat -100 0",  # truncated entry, no path
        ]
    )

    effects = bpf_trace.parse_bpf_effects(raw, workspace)

    assert effects == [Effect(str(workspace / "ok.txt"), EffectKind.READ)]


def test_parse_bpf_read_failure_quirk_matches_strace(tmp_path: Path) -> None:
    """Non-ENOENT failures still prove a read attempt (strace parity)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    raw = "\n".join(
        [
            _entry("stat", str(workspace / "denied.txt"), flags=0),
            _exit("stat", -13),  # EACCES
        ]
    )
    effects = bpf_trace.parse_bpf_effects(raw, workspace)
    assert effects == [Effect(str(workspace / "denied.txt"), EffectKind.READ)]

    write_missing = "\n".join(
        [
            _entry("openat", str(workspace / "new.txt"), flags=_WRITE_FLAGS),
            _exit("openat", -2),
        ]
    )
    assert bpf_trace.parse_bpf_effects(write_missing, workspace) == [
        Effect(str(workspace / "new.txt"), EffectKind.NEGATIVE)
    ]

    write_ok = "\n".join(
        [
            _entry("openat", str(workspace / "out.txt"), flags=_WRITE_FLAGS),
            _exit("openat", 4),
        ]
    )
    assert bpf_trace.parse_bpf_effects(write_ok, workspace) == []


def test_parse_bpf_openat2_flags_drive_read_detection(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    write_open = "\n".join(
        [
            _entry("openat2", str(workspace / "o2.txt"), flags=_WRITE_FLAGS),
            _exit("openat2", 5),
        ]
    )
    assert bpf_trace.parse_bpf_effects(write_open, workspace) == []


def test_build_script_restricts_to_available_tracepoints() -> None:
    available = {"sys_enter_openat", "sys_exit_openat"}
    script = bpf_trace.build_bpftrace_script(available)
    assert "tracepoint:sys_enter_openat" in script
    assert "tracepoint:sys_exit_openat" in script
    assert "sys_enter_stat" not in script
    assert "ATXBPF_READY" in script
    # process-call bodies belong to the exit side only (retval exists there)
    assert "args->ret" not in script.split("tracepoint:sys_exit")[0]


def test_build_script_resolved_section_is_optional() -> None:
    plain = bpf_trace.build_bpftrace_script()
    resolved = bpf_trace.build_bpftrace_script(with_resolved=True)
    assert "kprobe:vfs_open" not in plain
    assert "kprobe:vfs_open" in resolved and "dpath" in resolved


def test_select_tracepoints_filters_unknown_probes() -> None:
    enter, exit_ = bpf_trace.select_tracepoints(
        {"sys_enter_openat", "sys_exit_openat"}
    )
    assert enter == ("sys_enter_openat",)
    assert exit_ == ("sys_exit_openat",)
    enter_all, exit_all = bpf_trace.select_tracepoints(None)
    assert len(enter_all) == len(exit_all) == 24
    assert "sys_enter_openat" in enter_all


def test_bpf_static_available_matches_euid() -> None:
    ok, detail = bpf_trace.bpf_static_available()
    assert ok == (os.geteuid() == 0)
    assert detail  # non-empty detail in both cases


def test_resolve_trace_backend_auto_prefers_bpf_then_strace() -> None:
    backend, _ = bpf_trace.resolve_trace_backend("auto", strace_present=True,
                                                 bpf=(True, "ok"))
    assert backend == "bpf"
    backend, _ = bpf_trace.resolve_trace_backend("auto", strace_present=True,
                                                 bpf=(False, "no root"))
    assert backend == "strace"
    with pytest.raises(RuntimeError, match="requires strace or a working eBPF"):
        bpf_trace.resolve_trace_backend("auto", strace_present=False,
                                        bpf=(False, "no root"))


def test_resolve_trace_backend_explicit_modes_fail_closed() -> None:
    with pytest.raises(RuntimeError, match="'strace' requested"):
        bpf_trace.resolve_trace_backend("strace", strace_present=False)
    with pytest.raises(RuntimeError, match="'bpf' requested"):
        bpf_trace.resolve_trace_backend("bpf", strace_present=True,
                                        bpf=(False, "no root"))


def test_semisolate_rejects_unknown_backend(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="trace_backend"):
        SharedSemisolate(
            workspace=tmp_path,
            sandbox_dir=tmp_path / "session",
            trace_backend="bogus",
        )


def test_semisolate_bpf_backend_fails_closed_without_bpf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        bpf_trace, "bpf_static_available", lambda: (False, "no root")
    )
    with pytest.raises(RuntimeError, match="eBPF"):
        SharedSemisolate(
            workspace=tmp_path,
            sandbox_dir=tmp_path / "session",
            trace_backend="bpf",
        )


def test_semisolate_fails_closed_without_any_tracer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(semisolate_module.shutil, "which", lambda _: None)
    monkeypatch.setattr(
        bpf_trace, "bpf_static_available", lambda: (False, "no root")
    )
    with pytest.raises(RuntimeError, match="requires strace or a working eBPF"):
        SharedSemisolate(
            workspace=tmp_path,
            sandbox_dir=tmp_path / "session",
        )


def test_release_fifo_releases_blocked_reader(tmp_path: Path) -> None:
    fifo = tmp_path / "hold"
    os.mkfifo(fifo)
    reader = subprocess.Popen(
        ["bash", "-c", f'IFS= read -r _ < "{fifo}"; echo "released"'],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.2)
        assert reader.poll() is None  # reader is blocked
        pool = object.__new__(SharedSemisolate)  # bypass try probe
        pool._release_fifo(fifo, reader)
        stdout, _ = reader.communicate(timeout=10)
        assert stdout.strip() == "released"
    finally:
        if reader.poll() is None:
            reader.kill()
            reader.wait(timeout=5)


def test_run_step_bpf_orchestrates_fifo_ready_and_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the full eBPF step path with a stub bpftrace and a fake try."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    sandbox = tmp_path / "sandbox"
    (sandbox / "upperdir" / "tmp").mkdir(parents=True)

    fake_bpftrace = tmp_path / "fake-bpftrace"
    fake_bpftrace.write_text(
        "#!/bin/bash\n"
        "echo 'ATXBPF_READY 4242'\n"
        f"echo 'ATXBPF E 100 100 openat -100 524288 {workspace}/input.txt'\n"
        "echo 'ATXBPF X 100 100 openat 3'\n"
        "trap 'exit 0' INT TERM\n"
        "while true; do sleep 1; done\n",
        encoding="utf-8",
    )
    fake_bpftrace.chmod(0o755)

    real_popen = subprocess.Popen  # captured before the monkeypatch below

    class FakeTryPopen:
        """Fake only the try invocation; delegate everything else (bpftrace)."""

        def __init__(self, args, **kwargs) -> None:
            self.args = args
            if str(args[0]) == "/bin/true":
                self._real = None
                self.returncode = 0
                self.pid = 4242
            else:
                self._real = real_popen(args, **kwargs)

        def poll(self):
            return self._real.poll() if self._real is not None else 0

        def communicate(self, *args, **kwargs):
            if self._real is not None:
                return self._real.communicate(*args, **kwargs)
            return "stdout-text", "stderr-text"

        def send_signal(self, sig) -> None:
            if self._real is not None:
                self._real.send_signal(sig)

        def wait(self, timeout=None):
            if self._real is not None:
                return self._real.wait(timeout=timeout)
            return 0

        def kill(self) -> None:
            if self._real is not None:
                self._real.kill()

    monkeypatch.setattr(
        semisolate_module.shutil, "which", lambda _: str(fake_bpftrace)
    )
    monkeypatch.setattr(semisolate_module.subprocess, "Popen", FakeTryPopen)

    pool = object.__new__(SharedSemisolate)  # bypass try probe
    pool.workspace = workspace
    pool.sandbox_dir = sandbox
    pool.try_bin = Path("/bin/true")
    pool.trace_backend = "auto"
    pool.persistent_worker = False
    pool._step_count = 0
    pool._worker_failure_count = 0
    pool._worker_process = None
    pool._bpf_state = {
        "available": True,
        "script": "// fake script",
        "resolved": False,
        "detail": "fake",
    }

    cp, effects, duration = pool._run_step_bpf(
        ["bash", "-c", "true"], ["-N", str(sandbox)]
    )

    assert cp.returncode == 0
    assert cp.stdout == "stdout-text"
    assert effects == [Effect(str(workspace / "input.txt"), EffectKind.READ)]
    assert duration >= 0.0
    # the FIFO and the raw trace log are cleaned up
    assert list((sandbox / "upperdir" / "tmp").iterdir()) == []


def test_wait_for_bpftrace_ready_sees_marker(tmp_path: Path) -> None:
    log = tmp_path / "trace.raw"
    log.write_text("noise\nATXBPF_READY 77\nmore\n", encoding="utf-8")
    ready, _ = bpf_trace.wait_for_bpftrace_ready(log, 77, timeout=2.0)
    assert ready is True
    ready, _ = bpf_trace.wait_for_bpftrace_ready(log, 78, timeout=0.5)
    assert ready is False
    # A dead tracer process short-circuits the wait even before the timeout.
    empty = tmp_path / "empty.raw"
    empty.write_text("", encoding="utf-8")
    ready, _ = bpf_trace.wait_for_bpftrace_ready(
        empty, 77, timeout=30.0, abort_check=lambda: True
    )
    assert ready is False


def test_cli_exposes_trace_backend() -> None:
    args = build_parser().parse_args(
        ["begin", "--trace-backend", "bpf"]
    )
    assert args.trace_backend == "bpf"
    args = build_parser().parse_args(["begin"])
    assert args.trace_backend == "auto"
