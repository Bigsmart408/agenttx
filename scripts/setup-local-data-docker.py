#!/usr/bin/env python3
"""One-time local administrator setup for AgentTX's data-disk Docker service."""
import grp
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import time

DATA = Path('/mnt/data/agenttx-runtime')
CONFIG = Path('/etc/docker/agenttx-daemon.json')
UNIT = Path('/etc/systemd/system/agenttx-docker.service')
CONTAINERD_UNIT = Path('/etc/systemd/system/agenttx-containerd.service')
SOCKET = 'unix:///run/agenttx-docker/docker.sock'


def main():
    if os.geteuid() != 0:
        raise SystemExit('Run with sudo python3 scripts/setup-local-data-docker.py')
    if socket.gethostname() != 'r760-ssd':
        raise SystemExit('This setup is for the local r760-ssd host only.')
    mount = Path('/mnt/data')
    if not mount.is_mount() or mount.stat().st_dev == Path('/').stat().st_dev:
        raise SystemExit('/mnt/data must be a mounted data disk; refusing system-disk fallback.')
    account = pwd.getpwnam('bfq')
    group = grp.getgrgid(account.pw_gid).gr_name
    DATA.mkdir(exist_ok=True)
    os.chown(DATA, account.pw_uid, account.pw_gid)
    for name in ('cache', 'results', 'logs', 'tmp', 'downloads', 'validation', 'pip-cache', 'uv-cache', 'hf-cache', 'tiktoken-cache'):
        path = DATA / name
        path.mkdir(exist_ok=True)
        os.chown(path, account.pw_uid, account.pw_gid)
    # The shared system Docker daemon and its containers remain running.
    config = {
        'data-root': str(DATA / 'docker'),
        'exec-root': '/run/agenttx-docker/exec',
        'pidfile': '/run/agenttx-docker/docker.pid',
        'hosts': [SOCKET], 'group': group, 'storage-driver': 'overlay2',
        'containerd': '/run/agenttx-containerd/containerd.sock',
        'containerd-namespace': 'agenttx-moby',
        'containerd-plugins-namespace': 'agenttx-plugins',
        'registry-mirrors': ['https://docker.1ms.run', 'https://docker.1panel.live'],
        'max-concurrent-downloads': 2, 'max-download-attempts': 3,
        'bridge': 'none', 'iptables': False, 'ip6tables': False,
        'ip-forward': False, 'ip-masq': False,
    }
    unit = '''[Unit]
Description=AgentTX local Docker on /mnt/data
After=network-online.target agenttx-containerd.service
Wants=network-online.target
Requires=agenttx-containerd.service
RequiresMountsFor=/mnt/data

[Service]
Type=notify
RuntimeDirectory=agenttx-docker
RuntimeDirectoryMode=0755
ExecStart=/usr/bin/dockerd --config-file=/etc/docker/agenttx-daemon.json
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY http_proxy https_proxy all_proxy
Restart=on-failure
RestartSec=5
TimeoutStartSec=120
TimeoutStopSec=120
Delegate=yes
KillMode=process

[Install]
WantedBy=multi-user.target
'''
    containerd_unit = '''[Unit]
Description=AgentTX local containerd on /mnt/data
RequiresMountsFor=/mnt/data

[Service]
Type=notify
RuntimeDirectory=agenttx-containerd
ExecStart=/usr/bin/containerd --root /mnt/data/agenttx-runtime/containerd --state /run/agenttx-containerd/state --address /run/agenttx-containerd/containerd.sock
Restart=on-failure
RestartSec=5
TimeoutStartSec=120
Delegate=yes
KillMode=process

[Install]
WantedBy=multi-user.target
'''
    existing = {path: path.read_bytes() if path.exists() else None for path in (CONFIG, UNIT, CONTAINERD_UNIT)}
    if UNIT.exists() and 'AgentTX local Docker on /mnt/data' not in UNIT.read_text():
        raise SystemExit('Existing service has unexpected contents; refusing overwrite.')
    if CONFIG.exists() and json.loads(CONFIG.read_text()).get('data-root') != str(DATA / 'docker'):
        raise SystemExit('Existing dedicated daemon has a different data root; refusing overwrite.')
    if CONTAINERD_UNIT.exists() and 'AgentTX local containerd on /mnt/data' not in CONTAINERD_UNIT.read_text():
        raise SystemExit('Unexpected existing containerd unit; refusing overwrite.')
    active = subprocess.run(['systemctl', 'is-active', '--quiet', 'agenttx-docker.service']).returncode == 0
    if active:
        assert not subprocess.check_output(['docker', '-H', SOCKET, 'ps', '-q'], text=True).strip(), 'Dedicated task containers are running; defer restart.'
        subprocess.run(['systemctl', 'stop', 'agenttx-docker.service'], check=True, timeout=120)
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    candidate = CONFIG.with_suffix('.candidate.json')
    candidate.write_text(json.dumps(config, indent=2) + '\n')
    subprocess.run(['dockerd', '--validate', '--config-file', str(candidate)], check=True)
    candidate.replace(CONFIG)
    CONFIG.chmod(0o644)
    UNIT.write_text(unit)
    UNIT.chmod(0o644)
    CONTAINERD_UNIT.write_text(containerd_unit)
    CONTAINERD_UNIT.chmod(0o644)
    try:
        subprocess.run(['systemctl', 'daemon-reload'], check=True)
        subprocess.run(['systemctl', 'enable', '--now', 'agenttx-containerd.service'], check=True, timeout=130)
        subprocess.run(['systemctl', 'enable', '--now', 'agenttx-docker.service'], check=True, timeout=130)
        info = None
        for _ in range(20):
            p = subprocess.run(['docker', '-H', SOCKET, 'info', '--format', '{{json .}}'], capture_output=True, text=True, timeout=10)
            if p.returncode == 0:
                info = json.loads(p.stdout)
                break
            time.sleep(1)
        assert info and Path(info['DockerRootDir']).resolve() == (DATA / 'docker').resolve()
        assert not info.get('HTTPProxy') and not info.get('HTTPSProxy')
        assert info['Containerd']['Address'] == '/run/agenttx-containerd/containerd.sock'
        assert info['Containerd']['Namespaces']['Containers'] == 'agenttx-moby'
        record = dict(host=socket.gethostname(), docker_host=SOCKET,
                      docker_root=info['DockerRootDir'], storage_driver=info['Driver'],
                      mirrors=info['RegistryConfig']['Mirrors'],
                      containerd=info['Containerd'],
                      shared_docker_restarted=False, model_experiments_started=0)
        report = DATA / 'validation/local-docker-setup.json'
        report.write_text(json.dumps(record, indent=2) + '\n')
        os.chown(report, account.pw_uid, account.pw_gid)
        print(json.dumps(record, indent=2))
    except Exception:
        subprocess.run(['systemctl', 'disable', '--now', 'agenttx-docker.service'], check=False)
        subprocess.run(['systemctl', 'disable', '--now', 'agenttx-containerd.service'], check=False)
        for path, original in existing.items():
            if original is None:
                path.unlink(missing_ok=True)
            else:
                path.write_bytes(original)
        subprocess.run(['systemctl', 'daemon-reload'], check=False)
        raise


if __name__ == '__main__':
    main()
