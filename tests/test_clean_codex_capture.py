import json
import subprocess

from experiments.scripts.capture_clean_codex_in_image import (
    assemble_record,
    overlapping_write_exclusions,
    read_effects_by_window,
    workspace_snapshot,
)
from experiments.scripts.analyze_native_dependency_opportunity import analyze_trace


def test_codex_snapshot_and_one_native_turn(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "a.txt").write_text("old\n")
    subprocess.run(["git", "add", "a.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-q", "-m", "base"],
        cwd=tmp_path, check=True,
    )
    before = workspace_snapshot(tmp_path)
    (tmp_path / "a.txt").write_text("new\n")
    after_first = workspace_snapshot(tmp_path)
    (tmp_path / "b.txt").write_text("other\n")
    after_second = workspace_snapshot(tmp_path)
    trace = tmp_path / "syscalls.log"
    trace.write_text(
        f'123 1.100000 openat(AT_FDCWD, "{tmp_path}/a.txt", O_RDONLY) = 3<{tmp_path}/a.txt>\n'
    )
    windows = [
        {"start": 1.0, "end": 1.2, "item": {"id": "1", "type": "command_execution"}},
        {"start": 2.0, "end": 2.2, "item": {"id": "2", "type": "file_change"}},
    ]
    events = [{"type": "turn.completed", "usage": {"input_tokens": 10}}]
    record = assemble_record(
        "case", "tb", tmp_path, events, windows,
        [before, after_first, after_second], trace, 0, {},
    )
    assert len(record["conversation"]["turns"]) == 1
    assert len(record["ledger"]["steps"]) == 2
    assert {effect["kind"] for effect in record["ledger"]["steps"][0]["effects"]} == {"R", "W"}
    assert record["ledger"]["steps"][1]["effects"] == [
        {"path": str(tmp_path / "b.txt"), "kind": "W"}
    ]
    summary, roots = analyze_trace(json.loads(json.dumps(record)))
    assert summary["turns"] == 1
    assert len(roots) == 1


def test_codex_snapshot_records_rename_old_and_new_paths(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / "old.txt").write_text("content\n")
    subprocess.run(["git", "add", "old.txt"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
         "commit", "-q", "-m", "base"], cwd=tmp_path, check=True,
    )
    (tmp_path / "old.txt").rename(tmp_path / "new.txt")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    snapshot = workspace_snapshot(tmp_path)
    assert snapshot["old.txt"] == "deleted"
    assert snapshot["new.txt"] != "deleted"


def test_overlapping_reads_are_conservatively_attributed_to_both(tmp_path):
    trace = tmp_path / "trace.log"
    trace.write_text(
        f'123 2.000000 openat(AT_FDCWD, "{tmp_path}/input.txt", O_RDONLY) = 3<{tmp_path}/input.txt>\n'
    )
    windows = [
        {"start": 1.0, "end": 3.0},
        {"start": 1.5, "end": 2.5},
    ]
    groups = read_effects_by_window(trace, windows, tmp_path)
    assert groups[0] == groups[1]
    assert groups[0][0]["path"] == str(tmp_path / "input.txt")
    assert overlapping_write_exclusions(windows, [{"effects": group} for group in groups]) == []
    groups[1].append({"path": str(tmp_path / "out.txt"), "kind": "W"})
    assert overlapping_write_exclusions(windows, [{"effects": group} for group in groups]) == [
        "overlapping_mutating_tool_events"
    ]
