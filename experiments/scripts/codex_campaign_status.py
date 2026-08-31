#!/usr/bin/env python3
"""Write which of the 10 campaign tasks have causal tests_ok."""

from __future__ import annotations

import csv
from pathlib import Path

ROOT = Path("/home/pengpeng/agenttx")
OUT = ROOT / "experiments" / "results" / "codex_operator_testsok"
TASKS = [
    ("tb", "cancel-async-tasks"),
    ("tb", "llm-inference-batching-scheduler"),
    ("tb", "organization-json-generator"),
    ("tb", "recover-accuracy-log"),
    ("tb", "cross-entropy-method"),
    ("swe", "django__django-10914"),
    ("swe", "django__django-10924"),
    ("swe", "django__django-11039"),
    ("swe", "pallets__flask-4992"),
    ("swe", "pylint-dev__pylint-5859"),
]


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"true", "1", "yes"}


def _latest_causal(task: str) -> dict | None:
    newest = None
    newest_key = (-1, -1.0)
    for csv_path in ROOT.joinpath("experiments/results").glob(
        "crash_vs_checkpoint_testsok_*/official_tasks_raw.csv"
    ):
        try:
            with csv_path.open(newline="", encoding="utf-8") as handle:
                for row in csv.DictReader(handle):
                    if row.get("task") != task:
                        continue
                    if str(row.get("mode") or "") != "causal":
                        continue
                    try:
                        tokens = int(row.get("prompt_tokens") or 0)
                    except ValueError:
                        tokens = 0
                    if tokens <= 0:
                        continue
                    mtime = csv_path.stat().st_mtime
                    # Once a task has a valid official pass, later exploratory
                    # retries must not regress campaign progress.  Prefer a
                    # passing causal row, then the newest row within that tier.
                    key = (1 if _truthy(row.get("tests_ok")) else 0, mtime)
                    if key >= newest_key:
                        newest_key = key
                        newest = dict(row)
                        newest["_csv"] = str(csv_path)
        except (OSError, csv.Error):
            continue
    return newest


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    solved: list[str] = []
    pending: list[str] = []
    lines = ["# campaign progress", ""]
    for suite, task in TASKS:
        row = _latest_causal(task)
        ok = bool(row and _truthy(row.get("tests_ok")))
        if ok:
            solved.append(task)
            lines.append(f"- SOLVED {suite} {task} tokens={row.get('prompt_tokens')} csv={row.get('_csv')}")
        else:
            pending.append(task)
            if row:
                lines.append(
                    f"- PENDING {suite} {task} tests_ok={row.get('tests_ok')} "
                    f"tokens={row.get('prompt_tokens')} csv={row.get('_csv')}"
                )
            else:
                lines.append(f"- PENDING {suite} {task} (no valid causal row yet)")
    lines.append("")
    lines.append(f"solved={len(solved)}/{len(TASKS)}")
    lines.append("next=" + (pending[0] if pending else "NONE"))
    text = "\n".join(lines) + "\n"
    (OUT / "progress.txt").write_text(text, encoding="utf-8")
    env = [
        f"ALL_SOLVED={'1' if not pending else '0'}",
        f"SOLVED_COUNT={len(solved)}",
        f"PENDING_COUNT={len(pending)}",
        "NEXT_TASK=" + (pending[0] if pending else ""),
        "PENDING_TASKS=" + ",".join(pending),
        "SOLVED_TASKS=" + ",".join(solved),
    ]
    (OUT / "progress.env").write_text("\n".join(env) + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
