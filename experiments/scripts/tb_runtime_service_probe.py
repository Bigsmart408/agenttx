"""Zero-model checks of original image services across transaction commands."""


def probe_task_service(harness, task):
    if task not in {'install-windows-xp', 'home-server-https'}:
        return {'applicable': False}
    tx = harness.tx
    if task.startswith('install-windows-'):
        # No guest, task solution, installation media or official tests are
        # used: exercise the original QEMU binary and its real Unix monitor.
        start = '''set -e
qemu=$(command -v qemu-system-i386 || command -v qemu-system-x86_64)
"$qemu" -machine none -nodefaults -display none -S -daemonize \
 -pidfile /tmp/agenttx-qemu-probe.pid \
 -monitor none -qmp unix:agenttx-runtime-monitor.sock,server,nowait
test -S agenttx-runtime-monitor.sock
'''
        check = '''import socket, json
s=socket.socket(socket.AF_UNIX)
s.settimeout(5)
s.connect('agenttx-runtime-monitor.sock')
f=s.makefile('rwb',buffering=0)
assert 'QMP' in json.loads(f.readline())
f.write(b'{"execute":"qmp_capabilities"}\\n')
while 'return' not in json.loads(f.readline()): pass
f.write(b'{"execute":"query-version"}\\n')
while True:
 reply=json.loads(f.readline())
 if 'return' in reply: break
assert 'qemu' in reply['return'], repr(reply)
print('qemu-monitor-live')
'''
        cleanup = 'kill "$(cat /tmp/agenttx-qemu-probe.pid)"; rm -f agenttx-runtime-monitor.sock'
        expected = 'qemu-monitor-live'
    else:
        start = '''set -e
mkdir -p /tmp/agenttx-nginx-probe
cat >/tmp/agenttx-nginx-probe/nginx.conf <<'CONFIG'
pid /tmp/agenttx-nginx-probe/nginx.pid;
error_log /tmp/agenttx-nginx-probe/error.log;
events {}
http {
 access_log off;
 server { listen 127.0.0.1:18943; location / { return 200 'nginx-live'; } }
}
CONFIG
nginx -p /tmp/agenttx-nginx-probe -c /tmp/agenttx-nginx-probe/nginx.conf
'''
        check = '''import urllib.request
assert urllib.request.urlopen('http://127.0.0.1:18943',timeout=5).read()==b'nginx-live'
print('nginx-live')
'''
        cleanup = 'nginx -p /tmp/agenttx-nginx-probe -c /tmp/agenttx-nginx-probe/nginx.conf -s quit'
        expected = 'nginx-live'
    launch = tx.run_tool('runtime-service-preflight-start', ['bash', '-c', start], timeout_s=15)
    if launch.returncode:
        raise RuntimeError('original service start failed: ' + launch.stderr[-2000:])
    try:
        # Two distinct later commands exercise repeated snapshots and a trusted
        # untraced verifier while the same original service remains alive.
        for traced in (True, False):
            verdict = tx.run_tool('runtime-service-preflight-check',
                ['python3', '-c', check], trace_reads=traced, timeout_s=15)
            if verdict.returncode or expected not in verdict.stdout:
                raise RuntimeError('service lost across tool/snapshot boundary: ' + verdict.stderr[-2000:])
    finally:
        tx.run_tool('runtime-service-preflight-stop', ['bash', '-c', cleanup], timeout_s=15)
    return {'applicable': True, 'task': task, 'original_binary': True,
            'survived_snapshots': 2, 'trusted_verifier_connected': True, 'model_calls': 0}
