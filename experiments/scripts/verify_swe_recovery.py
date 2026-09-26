#!/usr/bin/env python3
"""Path-blind verifier for the synthetic recovery fault."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    args = parser.parse_args()
    marker = b"AGENTTX_INJECTED_FAULT"
    skip = {".git", ".venv", ".tmp", ".cache", "node_modules", "__pycache__"}
    for path in args.workdir.resolve().rglob("*"):
        if not path.is_file() or skip.intersection(path.parts):
            continue
        try:
            if path.stat().st_size <= 2_000_000 and marker in path.read_bytes():
                print("recovery verifier: injected crash is still present")
                return 86
        except OSError:
            continue
    print("recovery verifier: injected crash removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
