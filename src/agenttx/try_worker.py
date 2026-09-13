"""Persistent command worker used inside one try sandbox."""
from __future__ import annotations

import base64
import json
import os
import signal
import struct
import subprocess
import sys
import tempfile
import time
from typing import BinaryIO, Optional

_MAX_FRAME = 128 * 1024 * 1024
_CAPTURE_CAP_BYTES = 8_000_000


def _read_exact(stream: BinaryIO, size: int) -> Optional[bytes]:
    chunks = bytearray()
    while len(chunks) < size:
        chunk = stream.read(size - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


def _read_frame(stream: BinaryIO) -> Optional[dict]:
    header = _read_exact(stream, 4)
    if header is None:
        return None
    (size,) = struct.unpack("!I", header)
    if size > _MAX_FRAME:
        raise ValueError(f"worker frame too large: {size}")
    body = _read_exact(stream, size)
    if body is None:
        return None
    value = json.loads(body.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("worker request must be an object")
    return value


def _write_frame(stream: BinaryIO, value: dict) -> None:
    body = json.dumps(value, separators=(",", ":")).encode("utf-8")
    if len(body) > _MAX_FRAME:
        raise ValueError(f"worker frame too large: {len(body)}")
    stream.write(struct.pack("!I", len(body)))
    stream.write(body)
    stream.flush()


def _encoded(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _read_capped(stream: BinaryIO) -> bytes:
    stream.seek(0)
    data = stream.read(_CAPTURE_CAP_BYTES)
    if stream.read(1):
        data += b"\n[agenttx: output truncated]\n"
    return data


def _kill_process_group(process: subprocess.Popen) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.kill()
    except ProcessLookupError:
        pass


def main() -> int:
    stdin = sys.stdin.buffer
    protocol_stdout = sys.stdout.buffer
    _write_frame(protocol_stdout, {"ready": True})
    while True:
        request = _read_frame(stdin)
        if request is None:
            return 0
        if request.get("op") == "shutdown":
            _write_frame(protocol_stdout, {"stopped": True})
            return 0
        argv = request.get("argv")
        cwd = request.get("cwd")
        if not isinstance(argv, list) or not all(isinstance(item, str) for item in argv):
            _write_frame(stdout, {"error": "invalid argv"})
            continue
        hold_marker = request.get("hold_marker")
        if isinstance(hold_marker, str):
            # eBPF backend: block the step until the host-side tracer has
            # attached all probes and flipped the release marker to "go".
            # Polling a marker file's content instead of reading a FIFO:
            # FIFO pipe pairing does not cross the OverlayFS mount boundary
            # (pipes are allocated against the superblock's user namespace),
            # so a sandbox-side reader never sees a host-side writer.
            while True:
                try:
                    with open(hold_marker, "r", encoding="utf-8") as marker:
                        if marker.read() == "go":
                            break
                except OSError:
                    pass
                time.sleep(0.01)
        timeout_value = request.get("timeout_s")
        timeout_s = (
            float(timeout_value)
            if isinstance(timeout_value, (int, float)) and timeout_value > 0
            else None
        )
        try:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(
                    argv,
                    cwd=cwd if isinstance(cwd, str) else None,
                    # External harnesses such as Codex treat an open stdin as a
                    # request to append more prompt text.  The worker protocol
                    # owns its stdin, so child tools must see EOF.
                    stdin=subprocess.DEVNULL,
                    stdout=stdout_file,
                    stderr=stderr_file,
                    start_new_session=True,
                )
                timed_out = False
                try:
                    process.wait(timeout=timeout_s)
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _kill_process_group(process)
                    try:
                        process.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                stdout = _read_capped(stdout_file)
                stderr = _read_capped(stderr_file)
                if timed_out:
                    returncode = 124
                    stderr += (
                        f"\nexternal harness timeout after {timeout_s:.1f}s"
                    ).encode("utf-8")
                else:
                    returncode = 1 if process.returncode is None else process.returncode
            response = {
                "returncode": returncode,
                "stdout": _encoded(stdout),
                "stderr": _encoded(stderr),
            }
        except OSError as exc:
            response = {"returncode": 127, "stdout": "", "stderr": str(exc)}
        _write_frame(protocol_stdout, response)


if __name__ == "__main__":
    raise SystemExit(main())
