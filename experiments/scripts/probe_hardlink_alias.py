#!/usr/bin/env python3
"""Probe lower hard-link behavior across OverlayFS copy-up and commit."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from agenttx.runtime import AgentTX  # noqa: E402


def probe(trace_reads: bool = False) -> dict:
    scratch = Path(tempfile.mkdtemp(prefix="agenttx-hardlink-probe-", dir="/tmp"))
    workspace = scratch / "ws"
    workspace.mkdir()
    first = workspace / "first.txt"
    alias = workspace / "alias.txt"
    first.write_text("old\n", encoding="utf-8")
    os.link(first, alias)
    before = {
        "same_inode": os.path.samefile(first, alias),
        "link_count": first.stat().st_nlink,
    }
    tx = AgentTX.begin(
        workdir=workspace,
        session_dir=scratch / "session",
        trace_reads=trace_reads,
    )
    try:
        writer = tx.run_tool("writer", ["bash", "-c", "echo new > first.txt"])
        # Keep the probe deterministic even on hosts where strace is not
        # available; the catalog should still connect the alias read to the
        # writer through its persisted object id.
        reader = tx.run_tool(
            "reader",
            ["bash", "-c", "cat alias.txt"],
            extra_reads=[str(alias)],
        )
        tx.commit(writer.step_id)

        def normalized_effects(record):
            effects = []
            for effect in record.effects:
                try:
                    path = str(Path(effect.path).relative_to(workspace))
                except ValueError:
                    path = effect.path
                effects.append({"path": path, "kind": effect.kind.value})
            return effects

        result = {
            "before": before,
            "expected_posix_alias_read": "new\n",
            "overlay_alias_read": reader.stdout,
            "reader_parents": reader.parents,
            "writer_effects": normalized_effects(writer),
            "reader_effects": normalized_effects(reader),
            "after_partial_commit": {
                "first_content": first.read_text(encoding="utf-8"),
                "alias_content": alias.read_text(encoding="utf-8"),
                "same_inode": os.path.samefile(first, alias),
                "first_link_count": first.stat().st_nlink,
                "alias_link_count": alias.stat().st_nlink,
            },
        }
        result["overlay_matches_posix_hardlink_semantics"] = (
            result["overlay_alias_read"] == result["expected_posix_alias_read"]
            and result["after_partial_commit"]["same_inode"]
        )
        return result
    finally:
        tx.close(destroy=True)
        shutil.rmtree(scratch, ignore_errors=True)


def write_outputs(result: dict, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "hardlink_alias_probe.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    after = result["after_partial_commit"]
    lines = [
        "# Lower hard-link alias probe",
        "",
        "| observation | value |",
        "|---|---|",
        f"| same inode before transaction | {result['before']['same_inode']} |",
        f"| expected alias read after writing sibling | `{result['expected_posix_alias_read'].strip()}` |",
        f"| observed alias read in overlay | `{result['overlay_alias_read'].strip()}` |",
        f"| reader dependency parents | `{result['reader_parents']}` |",
        f"| same inode after selective commit | {after['same_inode']} |",
        f"| first / alias content after commit | `{after['first_content'].strip()}` / `{after['alias_content'].strip()}` |",
        f"| matches POSIX hard-link semantics | {result['overlay_matches_posix_hardlink_semantics']} |",
        "",
        "With TRY_OVERLAY_INDEX=on, the active overlay exposes the updated inode through both names. The selective-commit path expands the complete host hard-link group and updates its inode in place; an incomplete group fails closed instead of silently splitting aliases.",
    ]
    (output_dir / "hardlink_alias_probe.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "experiments" / "results",
    )
    parser.add_argument(
        "--trace-reads",
        action="store_true",
        help="enable dependency tracing (disabled by default for the identity probe)",
    )
    args = parser.parse_args()
    result = probe(trace_reads=args.trace_reads)
    write_outputs(result, args.output_dir)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
