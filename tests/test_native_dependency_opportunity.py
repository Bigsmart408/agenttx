import csv
import json
import sys

from experiments.scripts.analyze_native_dependency_opportunity import (
    analyze_trace, main, trace_quality,
)


def test_cross_turn_dependency_keeps_interleaved_independent_write():
    root = "/tmp/native/workspace"
    data = {
        "task": "case-1",
        "workspace_root": root,
        "source_system": "test",
        "source_completed": True,
        "ledger": {
            "steps": [
                {"step_id": 0, "effects": [{"kind": "W", "path": f"{root}/a.py"}]},
                {"step_id": 1, "effects": [{"kind": "W", "path": f"{root}/b.py"}]},
                {"step_id": 2, "effects": [{"kind": "R", "path": f"{root}/a.py"}]},
                {"step_id": 3, "effects": [{"kind": "W", "path": "/root/state.json"}]},
            ]
        },
        "conversation": {
            "turns": [
                {"calls": [{"step_id": 0}]},
                {"calls": [{"step_id": 1}]},
                {"calls": [{"step_id": 2}, {"step_id": 3}]},
            ]
        },
    }
    summary, rows = analyze_trace(data)

    assert summary["observed_workspace_edges"] == 1
    assert summary["max_edge_turn_span"] == 2
    assert summary["longest_observed_dependency_path"] == 2
    assert summary["mutating_steps"] == 2
    root_zero = next(row for row in rows if row["root_step"] == 0)
    assert root_zero["closure_steps"] == 2
    assert root_zero["temporal_extra_mutating_steps"] == 1
    assert root_zero["long_chain_with_interleaved_independent_work"] is True


def test_checkpoint_removes_only_turns_with_affected_steps():
    root = "/tmp/native/workspace"
    data = {
        "task": "nonadjacent-affected-turns", "workspace_root": root,
        "source_completed": True,
        "ledger": {"steps": [
            {"step_id": 0, "effects": [{"kind": "W", "path": f"{root}/a.py"}]},
            {"step_id": 1, "effects": [{"kind": "W", "path": f"{root}/b.py"}]},
            {"step_id": 2, "effects": [{"kind": "R", "path": f"{root}/a.py"}]},
            {"step_id": 3, "effects": [{"kind": "W", "path": f"{root}/c.py"}]},
        ]},
        "conversation": {"turns": [
            {"calls": [{"step_id": 0}]},
            {"calls": [{"step_id": 1}]},
            {"calls": [{"step_id": 2}, {"step_id": 3}]},
        ]},
    }

    _summary, rows = analyze_trace(data)
    root_zero = next(row for row in rows if row["root_step"] == 0)
    assert root_zero["closure_steps"] == 2
    assert root_zero["checkpoint_extra_mutating_steps"] == 1
    assert root_zero["temporal_extra_mutating_steps"] == 2


def test_negative_lookup_depends_on_prior_delete():
    root = "/tmp/native/workspace"
    data = {
        "task": "negative-lookup", "workspace_root": root,
        "source_completed": True,
        "ledger": {"steps": [
            {"step_id": 0, "effects": [{"kind": "D", "path": f"{root}/gone.txt"}]},
            {"step_id": 1, "effects": [{"kind": "N", "path": f"{root}/gone.txt"}]},
        ]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}, {"step_id": 1}]}]},
    }
    summary, _rows = analyze_trace(data)
    assert summary["observed_workspace_edges"] == 1


def test_failed_official_tests_do_not_invalidate_real_steps():
    root = "/tmp/native/workspace"
    data = {
        "task": "failed-tests", "workspace_root": root,
        "source_completed": True, "source_success": False,
        "verifier": {"runtime": "official_docker", "tests_rc": 1,
                     "tests_ok": False, "infrastructure_error": False},
        "ledger": {"steps": [
            {"step_id": 0, "effects": [{"kind": "W", "path": f"{root}/a.py"}]},
            {"step_id": 1, "effects": [{"kind": "W", "path": f"{root}/b.py"}]},
        ]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}, {"step_id": 1}]}]},
    }
    summary, rows = analyze_trace(data)
    assert summary["source_completed"] is True
    assert summary["source_success"] is False
    assert summary["candidate_roots"] == 1
    assert rows[0]["temporal_extra_mutating_steps"] == 1
    assert summary["trace_quality"] == "complete"


def test_commit_failure_preserves_observed_steps_as_prefix():
    root = "/tmp/native/workspace"
    data = {
        "task": "commit-blocked", "workspace_root": root,
        "source_system": "SWE-agent native", "source_completed": False,
        "model_finished": True, "termination_reason": "submitted",
        "commit_error": "PermissionError: outside workspace",
        "ledger": {"steps": [
            {"step_id": 0, "effects": [{"kind": "W", "path": f"{root}/a.py"}]},
            {"step_id": 1, "effects": [{"kind": "W", "path": f"{root}/b.py"}]},
        ]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}, {"step_id": 1}]}]},
    }
    summary, rows = analyze_trace(data)
    assert summary["trace_quality"] == "prefix"
    assert summary["steps"] == 2
    assert rows[0]["temporal_extra_mutating_steps"] == 1


def test_unattributed_mutating_events_are_invalid_even_with_steps():
    data = {
        "capture_valid": False, "source_completed": False,
        "ledger": {"steps": [{"step_id": 0, "effects": []}]},
    }
    assert trace_quality(data) == "invalid"


def test_missing_swe_target_test_keeps_trace_but_not_failure_label():
    root = "/tmp/native/workspace"
    data = {
        "task": "missing-test", "suite": "swe_bench_lite",
        "workspace_root": root, "source_completed": True,
        "source_success": False,
        "verifier": {
            "runtime": "official_docker", "tests_rc": 4,
            "infrastructure_error": False,
            "stderr": "ERROR: not found: /testbed/tests/test_case.py::test_new",
        },
        "ledger": {"steps": [{"step_id": 0, "effects": []}]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}]}]},
    }
    summary, _rows = analyze_trace(data)
    assert summary["source_completed"] is True
    assert summary["source_success"] is None
    assert summary["verifier_infrastructure_error"] is True
    assert summary["verifier_protocol_error"] is True


def test_old_swe_verifier_pass_without_test_patch_is_unknown():
    root = "/tmp/native/workspace"
    data = {
        "task": "old-pass", "suite": "swe_bench_lite",
        "workspace_root": root, "source_completed": True,
        "source_success": True,
        "verifier": {"runtime": "official_docker", "tests_rc": 0,
                     "tests_ok": True},
        "ledger": {"steps": [{"step_id": 0, "effects": []}]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}]}]},
    }
    summary, _rows = analyze_trace(data)
    assert summary["source_success"] is None
    assert summary["verifier_protocol_error"] is True
    assert summary["verifier_infrastructure_error"] is False


def test_web_search_barrier_prevents_false_independent_survivor():
    root = "/tmp/native/workspace"
    data = {
        "task": "search-barrier", "workspace_root": root,
        "source_completed": True,
        "ledger": {"steps": [
            {"step_id": 0, "effects": [{"kind": "W", "path": f"{root}/a.py"}]},
            {"step_id": 1, "effects": [], "opaque_context": True},
            {"step_id": 2, "effects": [{"kind": "W", "path": f"{root}/b.py"}]},
        ]},
        "conversation": {"turns": [{"calls": [
            {"step_id": 0}, {"step_id": 1}, {"step_id": 2},
        ]}]},
    }
    summary, rows = analyze_trace(data)
    assert summary["opaque_context_steps"] == 1
    assert summary["observed_workspace_edges"] == 0
    assert summary["longest_conservative_dependency_path"] == 3
    assert rows[0]["valid_later_mutating_steps"] == 0


def test_agent_format_exit_keeps_real_completed_steps():
    root = "/tmp/native/workspace"
    data = {
        "task": "format-exit", "workspace_root": root,
        "source_system": "SWE-agent native / Luna max / official Docker image",
        "source_completed": False, "termination_reason": "exit_format",
        "step_limit_reached": False,
        "ledger": {"steps": [
            {"step_id": 0, "effects": [{"kind": "W", "path": f"{root}/a.py"}]},
            {"step_id": 1, "effects": [{"kind": "W", "path": f"{root}/b.py"}]},
        ]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}, {"step_id": 1}]}]},
    }
    summary, rows = analyze_trace(data)
    assert summary["source_completed"] is False
    assert summary["trace_quality"] == "complete"
    assert rows[0]["temporal_extra_mutating_steps"] == 1


def test_duplicate_attempts_keep_both_verdicts(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    root = "/tmp/native/workspace"
    data = {
        "task": "same-task", "suite": "terminal_bench",
        "source_system": "Codex native", "workspace_root": root,
        "source_completed": True, "source_success": False,
        "ledger": {"steps": [
            {"step_id": 0, "effects": [{"kind": "W", "path": f"{root}/a.py"}]},
        ]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}]}]},
    }
    (first / "case.json").write_text(json.dumps(data))
    data["source_success"] = True
    (second / "case.json").write_text(json.dumps(data))
    output = tmp_path / "analysis"
    monkeypatch.setattr(sys, "argv", [
        "analyze", "--trace-dir", str(first), "--trace-dir", str(second),
        "--output-dir", str(output),
    ])
    assert main() == 0
    report = json.loads((output / "summary.json").read_text())
    assert report["tasks"] == 1
    assert report["recorded_trace_attempts"] == 2
    assert report["complete_trace_attempts"] == 2
    assert report["completed_tasks"] == 1
    assert report["duplicate_attempt_records"] == 1
    assert report["verified_failed_tasks"] == 1
    assert report["verified_successful_tasks"] == 1


def test_retry_does_not_erase_first_recorded_attempt(monkeypatch, tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    root = "/tmp/native/workspace"
    data = {
        "task": "same-task", "suite": "terminal_bench",
        "source_system": "SWE-agent native", "workspace_root": root,
        "source_completed": True, "source_success": None,
        "ledger": {"steps": [{"step_id": 0, "effects": []}]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}]}]},
    }
    (first / "case.json").write_text(json.dumps(data))
    data["source_success"] = False
    (second / "case.json").write_text(json.dumps(data))
    output = tmp_path / "analysis"
    monkeypatch.setattr(sys, "argv", [
        "analyze", "--trace-dir", str(first), "--trace-dir", str(second),
        "--output-dir", str(output),
    ])
    assert main() == 0
    report = json.loads((output / "summary.json").read_text())
    assert report["verified_failed_tasks"] == 1
    assert report["completed_without_verifier_result"] == 1


def test_capture_flagged_trace_is_still_counted(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    root = "/tmp/native/workspace"
    (traces / "case.json").write_text(json.dumps({
        "task": "invalid-capture", "suite": "terminal_bench",
        "source_system": "Codex native", "workspace_root": root,
        "source_completed": True, "capture_valid": False,
        "source_success": False,
        "ledger": {"steps": [{"step_id": 0, "effects": []}]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}]}]},
    }))
    output = tmp_path / "analysis"
    monkeypatch.setattr(sys, "argv", [
        "analyze", "--trace-dir", str(traces), "--output-dir", str(output),
    ])
    assert main() == 0
    report = json.loads((output / "summary.json").read_text())
    assert report["recorded_trace_attempts"] == 1
    assert report["capture_flagged_tasks"] == 1
    assert report["verified_failed_tasks"] == 1


def test_prefix_is_reported_and_included_in_recorded_opportunity(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    root = "/tmp/native/workspace"
    (traces / "case.json").write_text(json.dumps({
        "task": "commit-blocked", "suite": "terminal_bench",
        "source_system": "SWE-agent native", "workspace_root": root,
        "source_completed": False, "model_finished": True,
        "termination_reason": "submitted", "commit_error": "PermissionError",
        "ledger": {"steps": [
            {"step_id": 0, "effects": [{"kind": "W", "path": f"{root}/a.py"}]},
            {"step_id": 1, "effects": [{"kind": "W", "path": f"{root}/b.py"}]},
        ]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}, {"step_id": 1}]}]},
    }))
    output = tmp_path / "analysis"
    monkeypatch.setattr(sys, "argv", [
        "analyze", "--trace-dir", str(traces), "--output-dir", str(output),
    ])
    assert main() == 0
    report = json.loads((output / "summary.json").read_text())
    assert report["observed_prefix_tasks"] == 1
    assert report["recorded_trace_attempts"] == 1
    assert report["candidate_roots"] == 1
    with (output / "task_summary.csv").open() as handle:
        row = next(csv.DictReader(handle))
    assert row["trace_quality"] == "prefix"
    assert row["steps"] == "2"


def test_capture_flag_does_not_drop_recorded_step_opportunity(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    root = "/tmp/native/workspace"
    (traces / "case.json").write_text(json.dumps({
        "task": "overlap", "suite": "terminal_bench",
        "source_system": "Codex native", "workspace_root": root,
        "source_completed": False, "capture_valid": False,
        "capture_exclusions": ["overlapping_mutating_tool_events"],
        "ledger": {"steps": [
            {"step_id": 0, "effects": [{"kind": "W", "path": f"{root}/a.py"}]},
            {"step_id": 1, "effects": [{"kind": "W", "path": f"{root}/b.py"}]},
        ]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}, {"step_id": 1}]}]},
    }))
    output = tmp_path / "analysis"
    monkeypatch.setattr(sys, "argv", [
        "analyze", "--trace-dir", str(traces), "--output-dir", str(output),
    ])
    assert main() == 0
    report = json.loads((output / "summary.json").read_text())
    assert report["capture_flagged_tasks"] == 1
    assert report["candidate_roots"] == 1
    with (output / "root_opportunities.csv").open() as handle:
        row = next(csv.DictReader(handle))
    assert row["trace_quality"] == "invalid"


def test_cohort_manifest_excludes_extra_preflight(monkeypatch, tmp_path):
    traces = tmp_path / "traces"
    traces.mkdir()
    root = "/tmp/native/workspace"
    data = {
        "suite": "terminal_bench", "source_system": "Codex native",
        "workspace_root": root, "source_completed": True,
        "ledger": {"steps": [{"step_id": 0, "effects": []}]},
        "conversation": {"turns": [{"calls": [{"step_id": 0}]}]},
    }
    for task in ("selected", "preflight"):
        (traces / f"{task}.json").write_text(json.dumps({**data, "task": task}))
    cohort = tmp_path / "cohort.json"
    cohort.write_text(json.dumps({"phase1": {"tb": {"short": ["selected", "missing"]}}}))
    output = tmp_path / "analysis"
    monkeypatch.setattr(sys, "argv", [
        "analyze", "--trace-dir", str(traces), "--output-dir", str(output),
        "--cohort-manifest", str(cohort), "--cohort-suite", "tb",
    ])
    assert main() == 0
    report = json.loads((output / "summary.json").read_text())
    assert report["tasks"] == 1
    assert report["cohort_tasks"] == 2
    assert report["missing_cohort_tasks"] == ["missing"]
