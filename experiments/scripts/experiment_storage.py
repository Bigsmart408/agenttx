"""Explicit experiment storage paths; local profile keeps data off the code disk."""
import os
from pathlib import Path
import socket

ROOT = Path(__file__).resolve().parents[2]


def experiment_path(kind: str, *, project_root: Path | None = None) -> Path:
    if kind not in {'cache', 'results', 'logs'}:
        raise ValueError(kind)
    value = os.environ.get('AGENTTX_' + kind.upper() + '_DIR')
    data = os.environ.get('AGENTTX_DATA_ROOT')
    root = project_root if project_root is not None else ROOT
    target = Path(value) if value else Path(data) / kind if data else root / 'experiments' / kind
    if os.environ.get('AGENTTX_LOCAL_ONLY') == '1':
        if socket.gethostname() != 'r760-ssd':
            raise RuntimeError('Local-only execution requires host r760-ssd')
        mount = Path('/mnt/data')
        if not mount.is_mount() or mount.stat().st_dev == Path('/').stat().st_dev:
            raise RuntimeError('/mnt/data is not a mounted data disk')
        if not target.resolve().is_relative_to(mount.resolve()):
            raise RuntimeError(f'{kind} must be on /mnt/data: {target}')
        parent = target
        while not parent.exists():
            parent = parent.parent
        if parent.stat().st_dev != mount.stat().st_dev:
            raise RuntimeError(f'{kind} resolves to a different filesystem: {target}')
    return target
