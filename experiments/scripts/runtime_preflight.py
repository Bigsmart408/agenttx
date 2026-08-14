"""Fail-closed checks for experiments that need the AgentTX substrate."""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Optional


def runtime_preflight(root: Optional[Path] = None) -> Dict[str, object]:
    """Check dependencies and execute one real try overlay smoke test."""
    root = Path(root or Path(__file__).resolve().parents[2]).resolve()
    checks = []
    strace = shutil.which("strace")
    checks.append({"name": "strace", "ok": bool(strace), "detail": strace or "not found"})
    bpftrace = shutil.which("bpftrace")
    checks.append({
        "name": "bpftrace",
        "ok": bool(bpftrace),
        "detail": bpftrace or "not found (eBPF tracing backend unavailable; strace fallback used)",
        "required": False,
    })
    wrapper = root / "scripts" / "try-wrapper.sh"
    try_bin = root / "third_party" / "try" / "try"
    try_ready = wrapper.is_file() and os.access(wrapper, os.X_OK) and try_bin.is_file()
    checks.append({
        "name": "try_binary",
        "ok": try_ready,
        "detail": str(try_bin) if try_ready else "run scripts/bootstrap.sh",
    })
    if not try_ready:
        checks.append({
            "name": "root_overlay_permission",
            "ok": False,
            "detail": "try binary unavailable; run scripts/bootstrap.sh",
        })
        checks.append({
            "name": "try_overlay_execution",
            "ok": False,
            "detail": "not attempted because try_binary is unavailable",
        })
    else:
        with tempfile.TemporaryDirectory(prefix="agenttx-preflight-", dir="/tmp") as temp:
            sandbox = Path(temp) / "sandbox"
            sandbox.mkdir()
            try:
                result = subprocess.run(
                    [str(wrapper), "-N", str(sandbox), "--", "/bin/true"],
                    cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, timeout=60, check=False,
                )
                detail = (result.stderr or result.stdout or "").strip().splitlines()
                checks.append({
                    "name": "root_overlay_permission",
                    "ok": result.returncode == 0,
                    "detail": "unprivileged recursive-overlay sandbox" if result.returncode == 0
                    else "try overlay execution failed without root; rerun with sudo",
                })
                checks.append({
                    "name": "try_overlay_execution",
                    "ok": result.returncode == 0,
                    "detail": detail[-1][-500:] if detail else f"rc={result.returncode}",
                })
            except (OSError, subprocess.SubprocessError) as exc:
                checks.append({
                    "name": "root_overlay_permission",
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                })
                checks.append({
                    "name": "try_overlay_execution",
                    "ok": False,
                    "detail": f"{type(exc).__name__}: {exc}",
                })
    return {
        "root": str(root),
        "ok": all(
            bool(check["ok"]) for check in checks if not check.get("required", True)
        ),
        "checks": checks,
    }


def format_preflight(report: Dict[str, object]) -> str:
    lines = [f"runtime preflight: {'ok' if report['ok'] else 'blocked'}"]
    for check in report["checks"]:
        status = "ok" if check["ok"] else "FAIL"
        lines.append(f"- {status}: {check['name']}: {check['detail']}")
    return "\n".join(lines)
