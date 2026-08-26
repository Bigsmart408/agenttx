"""Persistent command worker used inside one try sandbox."""
from __future__ import annotations

import base64
import json
import struct
import subprocess
import sys
import time
from typing import BinaryIO, Optional

_MAX_FRAME = 128 * 1024 * 1024


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


def main() -> int:
    stdin = sys.stdin.buffer
    stdout = sys.stdout.buffer
    _write_frame(stdout, {"ready": True})
    while True:
        request = _read_frame(stdin)
        if request is None:
            return 0
        if request.get("op") == "shutdown":
            _write_frame(stdout, {"stopped": True})
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
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd if isinstance(cwd, str) else None,
                # External harnesses such as Codex treat an open stdin as a
                # request to append more prompt text.  The worker protocol
                # owns its stdin, so child tools must see EOF instead of the
                # worker pipe or a non-interactive task can wait forever.
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            response = {
                "returncode": completed.returncode,
                "stdout": _encoded(completed.stdout),
                "stderr": _encoded(completed.stderr),
            }
        except OSError as exc:
            response = {"returncode": 127, "stdout": "", "stderr": str(exc)}
        _write_frame(stdout, response)


if __name__ == "__main__":
    raise SystemExit(main())
