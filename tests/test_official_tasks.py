from experiments.scripts.bench_official_tasks import (
    POLICY_MODES,
    _apply_policy,
    _retained_artifacts_unchanged,
    official_group,
    summarize,
    token_summaries,
    write_outputs,
)
from experiments.workloads.recovery_inject import (
    DocSpec,
    build_recovery_manifest,
    document_content,
    recovery_manifest_json,
    render_recovery_manifest_prompt,
    retained_artifact_access,
)
from agenttx.ledger import Effect, EffectKind, Ledger


def test_policy_modes_are_the_original_trio():
    assert POLICY_MODES == ("causal", "temporal_checkpoint", "whole_branch_abort")


def test_apply_policy_rejects_crab_modes():
    class Tx:
        def rollback_causal(self, step):
            return [step]

        def rollback(self, step):
            return list(range(step + 1)) if step else [0]

    agent = type("A", (), {"harness": type("H", (), {"tx": Tx()})()})()
    assert _apply_policy(agent, "causal", 3) == [3]
    assert _apply_policy(agent, "temporal_checkpoint", 3) == [0, 1, 2, 3]
    assert _apply_policy(agent, "whole_branch_abort", 3) == [0]
    try:
        _apply_policy(agent, "chat_fs", 3)
    except ValueError:
        pass
    else:
        raise AssertionError("chat_fs must not be a recovery mode")


def test_official_group_keeps_published_labels():
    assert official_group({"suite": "swe", "repo": "django/django", "task": "x"}) == "django/django"
    assert official_group({"suite": "tb", "difficulty": "hard", "task": "maze"}) == "hard"
    assert official_group({"suite": "tb", "difficulty": "", "task": "maze"}) == "unspecified"


def _row(**kwargs):
    base = {
        "suite": "swe",
        "task": "django/django-1",
        "repo": "django/django",
        "difficulty": "",
        "category": "django/django",
        "version": "4.2",
        "scale": "short",
        "official_group": "django/django",
        "mode": "causal",
        "repeat": 0,
        "harness_backend": "deepseek_harness",
        "model": "deepseek-v4-flash",
        "success": True,
        "tests_ok": True,
        "independent_retained": True,
        "derived_removed": True,
        "total_tokens": 100,
        "prompt_tokens": 80,
        "completion_tokens": 20,
        "doc_replay_tokens": 0,
        "doc_replay_needed": False,
        "recovery_wall_s": 1.0,
        "usage_source": "dsh_session_jsonl",
    }
    base.update(kwargs)
    return base


def test_summarize_splits_official_and_length_axes():
    rows = [
        _row(mode="causal", total_tokens=100, success=True),
        _row(mode="temporal_checkpoint", total_tokens=180, success=True),
        _row(mode="whole_branch_abort", total_tokens=250, success=True),
        _row(
            suite="tb",
            task="hello-world",
            repo="terminal-bench:hello-world",
            difficulty="easy",
            category="file-operations",
            official_group="easy",
            scale="short",
            mode="causal",
            total_tokens=40,
            success=True,
        ),
        _row(
            suite="tb",
            task="hello-world",
            repo="terminal-bench:hello-world",
            difficulty="easy",
            category="file-operations",
            official_group="easy",
            scale="short",
            mode="temporal_checkpoint",
            total_tokens=90,
            success=False,
            tests_ok=False,
            independent_retained=False,
        ),
    ]
    summary = summarize(rows)
    groups = {(item["suite"], item["official_group"], item["mode"]): item for item in summary["official_group_summaries"]}
    assert groups[("swe", "django/django", "temporal_checkpoint")]["avoided_tokens_mean"] == 80
    # Failed temporal TB row is not a paired success, so no token savings.
    assert groups[("tb", "easy", "temporal_checkpoint")]["paired_success_repeats"] == 0
    assert groups[("tb", "easy", "temporal_checkpoint")]["success_rate"] == 0.0
    lengths = {(item["suite"], item["scale"], item["mode"]) for item in summary["length_summaries"]}
    assert ("swe", "short", "causal") in lengths


def test_failed_rows_do_not_enter_success_token_mean():
    rows = [
        _row(mode="causal", total_tokens=100, success=True, repeat=0),
        _row(mode="temporal_checkpoint", total_tokens=9999, success=False, repeat=0),
        _row(mode="temporal_checkpoint", total_tokens=150, success=True, repeat=1),
        _row(mode="causal", total_tokens=110, success=True, repeat=1),
    ]
    tokens = {item["mode"]: item for item in token_summaries(rows)}
    assert tokens["temporal_checkpoint"]["success_tokens_mean"] == 150
    assert tokens["temporal_checkpoint"]["paired_success_repeats"] == 1
    assert tokens["temporal_checkpoint"]["avoided_tokens_mean"] == 40


def test_write_outputs_emits_both_axes(tmp_path, monkeypatch):
    from experiments.scripts import bench_official_tasks as bench

    monkeypatch.setattr(bench, "_result_dir", lambda *args, **kwargs: tmp_path)
    summary = summarize([_row(), _row(mode="temporal_checkpoint", total_tokens=180)])
    out = write_outputs(summary, None, False, "deepseek_harness")
    assert (out / "official_group_summary.md").exists()
    assert (out / "length_summary.md").exists()
    assert (out / "official_token_summary.csv").exists()


def test_write_outputs_preserves_new_rem_columns_after_legacy_rows(tmp_path, monkeypatch):
    import csv
    from experiments.scripts import bench_official_tasks as bench

    monkeypatch.setattr(bench, "_result_dir", lambda *args, **kwargs: tmp_path)
    rows = [
        _row(repeat=0),
        _row(
            repeat=1,
            recovery_manifest_state_id="abc",
            recovery_manifest_authoritative=True,
            retained_read_effects=0,
        ),
    ]
    write_outputs(summarize(rows), None, False, "deepseek_harness")
    with (tmp_path / "official_tasks_raw.csv").open(newline="", encoding="utf-8") as handle:
        fields = next(csv.reader(handle))
    assert "recovery_manifest_state_id" in fields
    assert "recovery_manifest_authoritative" in fields
    assert "retained_read_effects" in fields


def test_axis_token_savings_pair_by_task_not_repeat():
    """Same official group, two instances, same repeat: pair by task."""
    rows = [
        _row(task="django/django-1", mode="causal", total_tokens=100, repeat=0),
        _row(task="django/django-1", mode="temporal_checkpoint", total_tokens=180, repeat=0),
        _row(task="django/django-2", mode="causal", total_tokens=50, repeat=0),
        _row(task="django/django-2", mode="temporal_checkpoint", total_tokens=70, repeat=0),
    ]
    summary = summarize(rows)
    groups = {
        (item["official_group"], item["mode"]): item
        for item in summary["official_group_summaries"]
        if item["suite"] == "swe"
    }
    # (180-100) and (70-50) -> mean 50.  Mixing by repeat would yield 75.
    assert groups[("django/django", "temporal_checkpoint")]["paired_success_repeats"] == 2
    assert groups[("django/django", "temporal_checkpoint")]["avoided_tokens_mean"] == 50


def test_summarize_tracks_isolated_doc_replay_tokens():
    rows = [
        _row(mode="causal", total_tokens=100, doc_replay_tokens=0, success=True),
        _row(mode="temporal_checkpoint", total_tokens=180, doc_replay_tokens=60, success=True),
        _row(mode="whole_branch_abort", total_tokens=250, doc_replay_tokens=90, success=True),
    ]
    groups = {(item["mode"]): item for item in summarize(rows)["task_summaries"]}
    assert groups["causal"]["success_doc_replay_tokens_mean"] == 0
    assert groups["temporal_checkpoint"]["success_doc_replay_tokens_mean"] == 60
    assert groups["temporal_checkpoint"]["avoided_replay_tokens_mean"] == 60
    assert groups["whole_branch_abort"]["avoided_replay_tokens_mean"] == 90


def test_independent_work_discarded_follows_ledger():
    from experiments.workloads.recovery_inject import independent_work_discarded

    injected = {"independent_steps": [1, 2]}

    class Step:
        def __init__(self, step_id, status):
            self.step_id = step_id
            self.status = status

    class Ledger:
        def __init__(self, steps):
            self.steps = steps

    retained = Ledger([Step(0, "speculative"), Step(1, "speculative"), Step(2, "speculative")])
    discarded = Ledger([Step(0, "rolled_back"), Step(1, "rolled_back"), Step(2, "rolled_back")])
    assert independent_work_discarded(injected, retained) is False
    assert independent_work_discarded(injected, discarded) is True


def test_summarize_saved_tokens_are_missing_doc_replay():
    rows = [
        _row(mode="causal", total_tokens=2_000_000, doc_replay_tokens=0, success=True),
        _row(mode="temporal_checkpoint", total_tokens=900_000, doc_replay_tokens=48_000, success=True),
        _row(mode="whole_branch_abort", total_tokens=1_050_000, doc_replay_tokens=33_000, success=True),
    ]
    groups = {item["mode"]: item for item in summarize(rows)["task_summaries"]}
    assert groups["causal"]["saved_tokens_mean"] == 0
    assert groups["temporal_checkpoint"]["saved_tokens_mean"] == 48000
    assert groups["whole_branch_abort"]["saved_tokens_mean"] == 33000
    assert groups["temporal_checkpoint"]["avoided_tokens_mean"] == -1_100_000


def _manifest_fixture(tmp_path, *, coarse=False, content=None):
    docs = [DocSpec("recovery_notes/design.md", "design", 2)]
    ledger = Ledger()
    root = ledger.add_step(
        "fault", [Effect(str(tmp_path / "pkg/core.py"), EffectKind.WRITE)]
    )
    note = ledger.add_step(
        "note", [Effect(str(tmp_path / docs[0].path), EffectKind.WRITE)]
    )
    derived = ledger.add_step(
        "derived",
        [Effect(str(tmp_path / "recovery_build/derived.txt"), EffectKind.WRITE)],
    )
    ledger.mark_rolled_back([root.step_id, derived.step_id])
    if coarse:
        ledger.mark_rolled_back([note.step_id])
        ledger.add_step(
            "doc-replay",
            [Effect(str(tmp_path / docs[0].path), EffectKind.WRITE)],
        )
    text = content if content is not None else document_content("design", 2, "task")
    manifest = build_recovery_manifest(
        policy="whole_branch_abort" if coarse else "causal",
        ledger=ledger,
        injected={
            "root_step": root.step_id,
            "faulty_path": "pkg/core.py",
            "independent_steps": [note.step_id],
            "derived_step": derived.step_id,
            "derived_paths": ["recovery_build/derived.txt"],
        },
        docs=docs,
        document_contents={docs[0].path: text},
        workdir=tmp_path,
        rollback_targets=[root.step_id, derived.step_id],
        path_exists={"pkg/core.py": True, "recovery_build/derived.txt": False},
    )
    return manifest, ledger, docs


def test_recovery_manifest_certifies_retained_state(tmp_path):
    manifest, _, _ = _manifest_fixture(tmp_path)
    assert manifest["authoritative"] is True
    artifact = manifest["retained"][0]
    assert artifact["state"] == "complete-protected"
    assert artifact["origin"] == "retained_by_causal_recovery"
    assert artifact["contract_valid"] is True
    assert len(artifact["sha256"]) == 64
    assert manifest["invalidated"][1]["current_state"] == "absent"
    assert recovery_manifest_json(manifest).endswith("\n")


def test_recovery_manifest_describes_final_replayed_state_not_old_loss(tmp_path):
    manifest, _, _ = _manifest_fixture(tmp_path, coarse=True)
    assert manifest["authoritative"] is True
    artifact = manifest["retained"][0]
    assert artifact["origin"] == "regenerated_after_recovery"
    prompt = render_recovery_manifest_prompt(manifest)
    assert "COMPLETE-PROTECTED" in prompt
    assert "regenerated_after_recovery" in prompt
    assert "was lost" not in prompt


def test_recovery_manifest_fails_closed_on_invalid_contract(tmp_path):
    manifest, _, _ = _manifest_fixture(tmp_path, content="# Design\nDESIGN-001: only one\n")
    assert manifest["authoritative"] is False
    assert "STATE MISMATCH" in render_recovery_manifest_prompt(manifest)


def test_recovery_manifest_rejects_valid_but_unexplained_workspace_file(tmp_path):
    docs = [DocSpec("recovery_notes/design.md", "design", 2)]
    ledger = Ledger()
    manifest = build_recovery_manifest(
        policy="causal",
        ledger=ledger,
        injected={"independent_steps": [99]},
        docs=docs,
        document_contents={docs[0].path: document_content("design", 2, "task")},
        workdir=tmp_path,
        rollback_targets=[],
        path_exists={},
    )
    assert manifest["retained"][0]["origin"] == "unexplained_workspace_state"
    assert manifest["authoritative"] is False


def test_recovery_state_id_changes_with_artifact_content(tmp_path):
    first, _, _ = _manifest_fixture(tmp_path)
    valid_but_different = (
        "# Design\nDESIGN-001: changed\nDESIGN-002: changed too\n"
    )
    second, _, _ = _manifest_fixture(tmp_path, content=valid_but_different)
    assert first["state_id"] != second["state_id"]


def test_retained_artifact_access_separates_reads_from_mutations(tmp_path):
    ledger = Ledger()
    path = str(tmp_path / "recovery_notes/design.md")
    ledger.add_step("before", [Effect(path, EffectKind.WRITE)])
    ledger.add_step("agent-read", [Effect(path, EffectKind.READ)])
    ledger.add_step("agent-write", [Effect(path, EffectKind.WRITE)])
    access = retained_artifact_access(
        ledger.steps,
        first_step=1,
        last_step=3,
        retained_paths=["recovery_notes/design.md"],
        workdir=tmp_path,
    )
    assert access == {
        "retained_paths_reopened": ["recovery_notes/design.md"],
        "retained_read_effects": 1,
        "retained_paths_modified": ["recovery_notes/design.md"],
    }


def test_retained_unchanged_uses_recovery_hash_not_template_text(tmp_path):
    path = tmp_path / "recovery_notes/design.md"
    path.parent.mkdir(parents=True)
    path.write_text("content with runtime newline\n\n", encoding="utf-8")
    import hashlib

    manifest = {
        "retained": [
            {"path": "recovery_notes/design.md", "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
        ]
    }
    assert _retained_artifacts_unchanged(tmp_path, manifest)
    path.write_text("changed\n", encoding="utf-8")
    assert not _retained_artifacts_unchanged(tmp_path, manifest)
