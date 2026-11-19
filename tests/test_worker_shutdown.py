import os
import subprocess
import sys
import threading
import time

import agenttx.semisolate as semisolate
from agenttx.semisolate import SharedSemisolate


def stop_pool(tmp_path, process, monkeypatch):
    pool = object.__new__(SharedSemisolate)
    pool._worker_process = process
    pool._bpf_persistent_proc = None
    pool.sandbox_dir = tmp_path / 'session'
    pool.sandbox_dir.mkdir()
    monkeypatch.setattr(semisolate, '_WORKER_SHUTDOWN_TIMEOUT_S', 0.1)
    # Regression failures must finish even if shutdown blocks on a pipe.
    watchdog = threading.Timer(2, process.kill)
    watchdog.start()
    started = time.monotonic()
    try:
        pool._stop_worker()
        assert time.monotonic() - started < 1.5
        assert process.poll() is not None
        assert pool._worker_process is None
    finally:
        watchdog.cancel()
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)
        process.stdin.close()
        process.stdout.close()


def test_shutdown_finishes_with_an_incomplete_command_response(tmp_path, monkeypatch):
    process = subprocess.Popen(
        [sys.executable, '-c',
         'import sys,time; sys.stdin.buffer.read(4); '
         'sys.stdout.buffer.write(b"\\x00\\x00"); sys.stdout.buffer.flush(); time.sleep(30)'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True,
    )
    stop_pool(tmp_path, process, monkeypatch)


def test_shutdown_finishes_when_request_pipe_is_full(tmp_path, monkeypatch):
    process = subprocess.Popen(
        [sys.executable, '-c', 'import time; time.sleep(30)'],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, start_new_session=True,
    )
    os.set_blocking(process.stdin.fileno(), False)
    try:
        while True:
            os.write(process.stdin.fileno(), b'x' * 4096)
    except BlockingIOError:
        pass
    stop_pool(tmp_path, process, monkeypatch)
