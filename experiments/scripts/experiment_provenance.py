"""Fingerprint execution sources, including uncommitted and untracked code."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path


def source_manifest(root: Path) -> dict:
    root = Path(root)
    paths = set()
    for directory in ("src", "scripts", "experiments/scripts", "experiments/workloads"):
        for path in (root / directory).rglob("*"):
            if path.is_file() and path.suffix in {".py", ".sh"} and "__pycache__" not in path.parts:
                paths.add(path)
    for relative in ("environment.yml", "requirements-agent.txt", "experiments/motivation_strategies.py", "third_party/try/try"):
        path = root / relative
        if path.is_file():
            paths.add(path)
    files = [{"path": str(path.relative_to(root)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
             for path in sorted(paths)]
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return {"schema": "agenttx-execution-sources-v1", "sha256": hashlib.sha256(encoded).hexdigest(), "files": files}
