import shlex
import time

from agenttx.ledger import Effect, EffectKind
from agenttx.runtime import AgentTX
from agenttx.trace import parse_strace_effects


def test_streaming_trace_keeps_child_cwd_and_split_syscalls(tmp_path):
    state = {}
    first = '10 chdir("sub") = 0\n10 clone(child_stack=NULL, flags=SIGCHLD) = 11\n11 openat(AT_FDCWD, "input", O_RDONLY <unfinished ...>\n'
    parse_strace_effects(first, tmp_path, state=state)
    effects = parse_strace_effects('11 <... openat resumed>) = 3\n', tmp_path, state=state)
    assert Effect(str(tmp_path / 'sub/input'), EffectKind.READ) in effects
    assert not parse_strace_effects('', tmp_path, state=state)


def test_background_server_survives_next_snapshot_and_official_style_verifier(tmp_path):
    workspace = tmp_path / 'app'
    workspace.mkdir()
    tx = AgentTX.begin(workdir=workspace, session_dir=tmp_path / 'session', trace_backend='strace')
    server = '''import socket, pathlib, os
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.bind("monitor.sock")
s.listen()
pathlib.Path("server.pid").write_text(str(os.getpid()))
while True:
 c,_=s.accept()
 with c:
  c.recv(8)
  c.sendall(pathlib.Path("input.txt").read_bytes())
'''
    command = 'printf response > input.txt; python3 -c ' + shlex.quote(server) + ' </dev/null >/tmp/agenttx-service-test.log 2>&1 &\nfor i in $(seq 1 100); do test -S monitor.sock && exit 0; sleep .02; done; exit 1'
    try:
        started = time.monotonic()
        launch = tx.run_tool('start-service', ['bash', '-c', command], timeout_s=10)
        assert launch.returncode == 0, launch.stderr
        assert time.monotonic() - started < 10
        client = 'import socket; s=socket.socket(socket.AF_UNIX); s.connect("monitor.sock"); s.sendall(b"request"); print(s.recv(100).decode())'
        verdict = tx.run_tool('official-verifier', ['python3', '-c', client], timeout_s=10)
        assert verdict.returncode == 0, verdict.stderr
        assert verdict.stdout.strip() == 'response'
        assert Effect(str(workspace / 'input.txt'), EffectKind.READ) in verdict.effects
        assert not any('worker-strace-' in e.path for e in verdict.effects)
        # Killing a timed-out foreground group must not kill another command's
        # service, and its next request must still be dependency-traced.
        timed = tx.run_tool('timeout-foreground', ['sleep', '30'], timeout_s=0.1)
        assert timed.returncode == 124
        again = tx.run_tool('verify-after-timeout', ['python3', '-c', client], timeout_s=10)
        assert again.returncode == 0 and again.stdout.strip() == 'response'
        assert Effect(str(workspace / 'input.txt'), EffectKind.READ) in again.effects
    finally:
        tx.close(destroy=True)
