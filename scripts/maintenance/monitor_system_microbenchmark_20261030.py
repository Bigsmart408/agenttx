"""Read-only campaign monitor: no model calls, retries, or worker mutations."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from zoneinfo import ZoneInfo

DOCKER = ["docker", "--host", os.environ.get("AGENTTX_DOCKER_HOST", "unix:///run/agenttx-docker/docker.sock")]


def storage_free_gib(path):
    path = Path(path)
    while True:
        try:
            return shutil.disk_usage(path).free / 1024**3
        except PermissionError:
            if path == path.parent:
                raise
            path = path.parent


def write_report(output, row):
    at = datetime.fromisoformat(row["at"]).astimezone(ZoneInfo("Asia/Shanghai"))
    lines = ["# 补充对照与上下文消融进展", "", at.strftime("北京时间 %Y-%m-%d %H:%M:%S"), "",
             f"运行阶段：`{row.get('phase', 'unknown')}`；当前 worker：{len(row.get('workers', []))}。", "",
             "| 策略 | 通过 | 任务失败 | 基础设施异常 | 运行中 | 待运行 |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for policy, counts in row.get("counts_by_policy", {}).items():
        lines.append("| " + policy + " | " + " | ".join(str(counts.get(k, 0)) for k in
                     ("passed", "task_failed", "infrastructure_error", "running", "pending")) + " |")
    lines += ["", "磁盘可用空间：" + "；".join(f"{k} {v:.1f} GiB" for k, v in row.get("storage_free_gib", {}).items()) + "。",
              "", "告警：" + ("；".join(row.get("alerts", [])) or "无") + "。", "",
              "各策略最终成本比较需使用相同 case/repeat 的配对数据。"]
    throughput = row.get("control_throughput", {})
    if throughput:
        lines += ["", f"三组对照有效完成 {throughput['completed']}/{throughput['total']}；"
                  f"最近一小时完成 {throughput['finished_last_hour']} 次，"
                  f"最近半小时完成 {throughput['finished_last_half_hour']} 次。"]
        remaining_hours = throughput.get("remaining_hours_at_last_hour_rate")
        if remaining_hours is not None:
            lines += [f"按最近一小时速度估算剩余约 {remaining_hours:.1f} 小时；案例长度变化会影响估计。"]
    target = output / "latest-report.md"
    tmp = target.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    tmp.replace(target)
    with (output / "reports.jsonl").open("a") as stream:
        stream.write(json.dumps(row, ensure_ascii=False) + "\n")


def timestamp(value):
    return datetime.fromisoformat(value).timestamp()


def command(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=25)
    if result.returncode:
        raise RuntimeError(f"{argv[0]} exited {result.returncode}: {result.stderr[-500:]}")
    return result.stdout


def write_json(path, row):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(row, indent=2) + "\n")
    tmp.replace(path)


def sample(args, cache):
    now = time.time()
    cohort = args.cohort.resolve()
    progress = json.loads((cohort / "progress.json").read_text())
    stages = json.loads((cohort / "cells.json").read_text())
    counts = {stage: dict(Counter(c["state"] for c in rows)) for stage, rows in stages.items()}
    active = [(stage, cell) for stage, rows in stages.items() for cell in rows
              if cell["state"] == "running"]
    finished = [c for rows in stages.values() for c in rows if c.get("finished_at")]
    last = max([timestamp(progress["started_at"]),
                *(timestamp(c["finished_at"]) for c in finished)])
    phase_done = progress["phase"] == "finished"
    health = dict(line.split("=", 1) for line in command([
        "systemctl", "--user", "show", args.service,
        "--property=ActiveState", "--property=SubState", "--property=MainPID"
    ]).splitlines() if "=" in line)
    containers = set(command(DOCKER + ["ps", "--filter", "name=atx-micro-",
                                    "--format", "{{.Names}}"]).splitlines())
    issues = []
    if progress.get("stop_reason"):
        issues.append("coordinator_stop:" + progress["stop_reason"])
    if not phase_done and health.get("ActiveState") != "active":
        issues.append("coordinator_not_active")
    age = now - timestamp(progress["updated_at"])
    if not phase_done and age > 180:
        issues.append("progress_stale_over_180s")
    if not phase_done and now - last >= 1800:
        issues.append("no_terminal_result_for_30min")
    docker_root = command(DOCKER + ["info", "--format", "{{.DockerRootDir}}"]).strip()
    storage = dict(results=storage_free_gib(cohort), docker=storage_free_gib(docker_root))
    free = storage["results"]
    for name, available in storage.items():
        if available < 150:
            issues.append(f"{name}_disk_free_below_150GiB")
        if available < 100:
            issues.append(f"{name}_disk_free_below_100GiB")
    if len(active) > progress["workers"]:
        issues.append("active_cells_exceed_worker_limit")
    workers = []
    for stage, cell in active:
        name = "atx-micro-" + hashlib.sha256(
            (str(cohort) + stage + cell["key"]).encode()).hexdigest()[:18]
        folder = cohort / stage / cell["key"] / cell["policy"]
        files = [folder / "api-usage.json", cohort / "logs" / (name + ".log")]
        activity = []
        inaccessible = []
        for path in files:
            try:
                activity.append(path.stat().st_mtime)
            except FileNotFoundError:
                pass
            except PermissionError:
                # Active root-owned OverlayFS results are chowned on completion.
                # The redacted top-level worker log remains observable meanwhile.
                inaccessible.append(path.name)
        latest_activity = max(activity, default=timestamp(cell["started_at"]))
        elapsed = now - timestamp(cell["started_at"])
        present = name in containers
        if elapsed > 60 and not present:
            issues.append("worker_container_missing:" + cell["key"])
        if now - latest_activity >= 1800:
            issues.append("worker_no_log_or_usage_for_30min:" + cell["key"])
        workers.append(dict(stage=stage, key=cell["key"], container=name,
                            container_present=present, elapsed_s=round(elapsed),
                            inaccessible_active_metadata=inaccessible,
                            log_or_usage_age_s=round(now - latest_activity)))

    # Only open known result files; never walk OverlayFS session trees.
    audits = []
    for stage in ("model_canary", "main"):
        for cell in stages[stage]:
            if not cell.get("finished_at"):
                continue
            result_path = cell.get("result")
            if not result_path:
                issues.append("missing_result:" + stage + "/" + cell["key"])
                continue
            path = Path(result_path)
            stamp = path.stat().st_mtime_ns
            if cache.get(result_path, (None,))[0] != stamp:
                row = json.loads(path.read_text())
                usage = row.get("api_usage", {})
                cache[result_path] = (stamp, dict(
                    stage=stage, key=cell["key"], usage_complete=bool(usage.get("complete")),
                    budget_exceeded=bool(usage.get("budget_exceeded")),
                    known_tokens=usage.get("known_total_tokens"),
                    verifier_passed=row.get("verifier_passed"),
                    command_audit_passed=row.get("command_audit_passed")))
            audit = cache[result_path][1]
            audits.append(audit)
            if not audit["usage_complete"]:
                issues.append("api_usage_incomplete:" + stage + "/" + cell["key"])
            if audit["budget_exceeded"]:
                issues.append("token_budget_exceeded:" + stage + "/" + cell["key"])
    main_audits = [r for r in audits if r["stage"] == "main"]
    policies = sorted({c["policy"] for c in stages["main"]})
    by_policy = {p: dict(Counter(c["state"] for c in stages["main"] if c["policy"] == p)) for p in policies}
    controls = [c for c in stages["main"] if c["policy"] != "causal_workspace_only"]
    completed = [c for c in controls if c["state"] in ("passed", "task_failed")]
    recent = [c for c in completed if c.get("finished_at") and now - timestamp(c["finished_at"]) <= 3600]
    throughput = dict(total=len(controls), completed=len(completed),
                      finished_last_hour=len(recent),
                      finished_last_half_hour=sum(now - timestamp(c["finished_at"]) <= 1800 for c in recent),
                      remaining_hours_at_last_hour_rate=(len(controls) - len(completed)) / len(recent) if recent else None)
    return dict(at=datetime.now(timezone.utc).isoformat(), cohort=str(cohort),
                phase=progress["phase"], counts=counts, stop_reason=progress.get("stop_reason"),
                counts_by_policy=by_policy, storage_free_gib=storage, docker_data_root=docker_root,
                control_throughput=throughput,
                coordinator=health, progress_age_s=round(age),
                seconds_without_terminal=round(now - last), disk_free_gib=round(free, 2),
                completed_main_audited=len(main_audits),
                complete_usage_main=sum(r["usage_complete"] for r in main_audits),
                known_finished_main_tokens=sum(r["known_tokens"] or 0 for r in main_audits),
                task_failures=[dict(stage=s, key=c["key"], state=c["state"])
                               for s, rows in stages.items() for c in rows
                               if c["state"] in ("task_failed", "infrastructure_error")],
                workers=workers, alerts=issues, no_model_calls=True, read_only=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cohort", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--interval", type=float, default=60)
    parser.add_argument("--report-interval", type=float, default=1800)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cache = {}
    previous_alerts = None
    previous_phase = None
    last_report = 0
    while True:
        try:
            row = sample(args, cache)
        except Exception as exc:
            row = dict(at=datetime.now(timezone.utc).isoformat(),
                       alerts=[f"monitor_error:{type(exc).__name__}:{exc}"],
                       no_model_calls=True, read_only=True)
        write_json(args.output / "latest.json", row)
        with (args.output / "samples.jsonl").open("a") as stream:
            stream.write(json.dumps(row) + "\n")
        if row["alerts"] != previous_alerts:
            with (args.output / "alert-events.jsonl").open("a") as stream:
                stream.write(json.dumps(dict(at=row["at"], alerts=row["alerts"])) + "\n")
        if (time.monotonic() - last_report >= args.report_interval or
                row["alerts"] != previous_alerts or row.get("phase") != previous_phase):
            write_report(args.output, row)
            last_report = time.monotonic()
        previous_alerts = row["alerts"]
        previous_phase = row.get("phase")
        print(json.dumps({k: v for k, v in row.items() if k != "workers"}), flush=True)
        if args.once or row.get("phase") == "finished":
            break
        time.sleep(max(1, args.interval))


if __name__ == "__main__":
    main()
