from subprocess import CompletedProcess
from types import SimpleNamespace
import subprocess
import json
from pathlib import Path

from experiments.scripts import capture_clean_native_traces as capture
from experiments.workloads import terminal_bench_suite as tb


def stub_native_tb_verifier(monkeypatch, tmp_path):
    source = tmp_path / "task-source"
    source.mkdir()
    script = source / "run-tests.sh"
    script.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    monkeypatch.setattr(tb, "task_source", lambda *_args: source)
    monkeypatch.setattr(tb, "prepare_official_test_script", lambda *_args: (script, None))
    monkeypatch.setattr(tb, "official_image_user", lambda _image: "root")


def write_native_verdict(tmp_path, stem, rc=0):
    pending = tmp_path / "_pending" / stem
    (pending / "verifier.rc").write_text(f"{rc}\n", encoding="utf-8")
    (pending / "verifier.stdout.log").write_text("ok\n", encoding="utf-8")
    (pending / "verifier.stderr.log").write_text("", encoding="utf-8")


def test_full_cohort_selection_excludes_phase_one_before_sharding(monkeypatch, tmp_path):
    monkeypatch.setattr(tb, "load_tasks", lambda _cache: {
        task: object() for task in ("c", "a", "b", "d", "e")
    })
    cohort = tmp_path / "cohort.json"
    cohort.write_text(json.dumps({"phase1": {"tb": {"short": ["b"]}}}))
    args = SimpleNamespace(
        suite="tb", all=True, tasks=None, cache_dir=tmp_path,
        exclude_cohort_manifest=cohort, shard_index=1, shard_count=2,
    )
    assert capture.selected_task_ids(args) == ["c", "e"]


def test_explicit_task_selection_preserves_requested_order(tmp_path):
    args = SimpleNamespace(
        suite="swe", all=False, tasks=["second", "first"],
        cache_dir=tmp_path, exclude_cohort_manifest=None,
        shard_index=0, shard_count=1,
    )
    assert capture.selected_task_ids(args) == ["second", "first"]


def test_official_terminal_verifier_uses_image(monkeypatch, tmp_path):
    task = SimpleNamespace(official_full=True, container_verifier=False, docs=lambda: ())
    calls = []
    monkeypatch.setattr(
        tb, "_verify_in_official_container",
        lambda workdir, task, cache: calls.append((workdir, cache))
        or CompletedProcess([], 0, "ok", ""),
    )
    result = tb.verify(
        tmp_path, task, "python", require_recovery_artifacts=False,
        cache_root=tmp_path,
    )
    assert result["tests_ok"]
    assert calls == [(tmp_path, tmp_path)]


def test_terminal_verifier_script_ignores_stale_tempfile_cache(monkeypatch, tmp_path):
    source = tmp_path / "task"
    source.mkdir()
    (source / "run-tests.sh").write_text(
        "curl -LsSf https://astral.sh/uv/0.7.13/install.sh | sh\n",
        encoding="utf-8",
    )
    local_uv = tmp_path / "terminal_bench/uv/installed/bin/uv"
    local_uv.parent.mkdir(parents=True)
    local_uv.write_text("uv", encoding="utf-8")
    (source / "tests").mkdir()
    workdir = tmp_path / "workspace"
    workdir.mkdir()
    monkeypatch.setattr(tb, "ensure_official_image", lambda *_: ("test-image", "/app"))
    monkeypatch.setattr(tb, "task_source", lambda *_: source)
    monkeypatch.setattr(tb.tempfile, "tempdir", str(tmp_path / "deleted-old-case"))
    copied_scripts = []

    def fake_docker_run(command, **_kwargs):
        if command[:2] == ["docker", "create"]:
            return CompletedProcess(command, 0, "container-id\n", "")
        if command[:2] == ["docker", "cp"] and command[-1].endswith("agenttx-run-tests.sh"):
            copied_scripts.append(Path(command[2]))
            assert copied_scripts[-1].is_file()
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(tb, "_docker_run", fake_docker_run)
    result = tb._verify_in_official_container(
        workdir, SimpleNamespace(task_id="task"), tmp_path,
    )
    assert result.returncode == 0
    assert len(copied_scripts) == 1
    assert copied_scripts[0].parent == Path("/tmp")


def test_terminal_baseline_indexes_unreadable_input_without_changing_modes(monkeypatch, tmp_path):
    commands = []

    def fake_run(command, **_kwargs):
        commands.append(command)
        if command[:3] == ["git", "add", "-A"]:
            return CompletedProcess(command, 128, "", "Permission denied")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    capture.initialize_terminal_baseline(tmp_path, "task-image")
    docker_command = next(command for command in commands if command[:2] == ["docker", "run"])
    assert "task-image" in docker_command
    assert "--network" in docker_command and "none" in docker_command
    assert "git -c safe.directory=/task-workspace add -A" in docker_command[-1]
    assert "chmod" not in docker_command[-1]


def test_terminal_capture_keeps_absolute_image_paths(monkeypatch, tmp_path):
    task = SimpleNamespace(instruction="Read /data/input and write /app/out")
    stub_native_tb_verifier(monkeypatch, tmp_path)
    monkeypatch.setattr(tb, "load_tasks", lambda _cache: {"example": task})
    def materialize(_task, workdir, _cache, **kwargs):
        assert kwargs == {"include_verifier_files": False}
        workdir.mkdir()

    monkeypatch.setattr(tb, "materialize", materialize)
    monkeypatch.setattr(tb, "ensure_official_image", lambda _task, _cache: ("test-image", "/app"))

    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "run"]:
            assert "--pull" in command and "never" in command
            assert "test-image" in command
            assert "--prompt-file" in command
            assert "AGENTTX_HOST_UID=" in " ".join(command)
            pending = tmp_path / "_pending" / "tb__example" / "native_traces"
            pending.mkdir(parents=True)
            (pending / "tb__example.json").write_text(
                '{"source_completed": true, "result": {"summary": "submitted"}}'
            )
            write_native_verdict(tmp_path, "tb__example")
        return CompletedProcess(command, 0)

    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    args = SimpleNamespace(
        agent="sweagent", cache_dir=tmp_path, output_dir=tmp_path, model="gpt-5.6-luna",
        reasoning_effort="max", max_steps=2, max_model_calls=2,
        task_timeout_s=3600,
    )
    record = capture.capture_terminal_in_image("example", args)
    assert record["source_success"] is True
    assert record["verifier"]["runtime"] == "same_official_task_container"


def test_terminal_capture_mounts_image_workdir(monkeypatch, tmp_path):
    task = SimpleNamespace(instruction="Work in the project directory")
    stub_native_tb_verifier(monkeypatch, tmp_path)
    monkeypatch.setattr(tb, "load_tasks", lambda _cache: {"nested": task})
    monkeypatch.setattr(
        tb, "materialize", lambda _task, workdir, _cache, **_kwargs: workdir.mkdir(),
    )
    monkeypatch.setattr(
        tb, "ensure_official_image", lambda _task, _cache: ("test-image", "/app/project"),
    )
    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "run"]:
            assert "--workdir" in command
            assert command[command.index("--workdir") + 1] == "/app/project"
            assert any("dst=/app/project" in part for part in command)
            pending = tmp_path / "_pending" / "tb__nested" / "native_traces"
            pending.mkdir(parents=True)
            (pending / "tb__nested.json").write_text(
                '{"source_completed": false, "result": {"summary": "exit_cost"}}'
            )
        return CompletedProcess(command, 0)
    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    args = SimpleNamespace(
        agent="sweagent", cache_dir=tmp_path, output_dir=tmp_path,
        model="gpt-5.6-luna", reasoning_effort="max",
        max_steps=2, max_model_calls=2, task_timeout_s=3600,
    )
    record = capture.capture_terminal_in_image("nested", args)
    assert record["source_completed"] is False


def test_invalid_trace_still_gets_official_verdict_if_model_finished(monkeypatch, tmp_path):
    task = SimpleNamespace(instruction="Create output")
    stub_native_tb_verifier(monkeypatch, tmp_path)
    monkeypatch.setattr(tb, "load_tasks", lambda _cache: {"case": task})
    monkeypatch.setattr(
        tb, "materialize", lambda _task, workdir, _cache, **_kwargs: workdir.mkdir(),
    )
    monkeypatch.setattr(
        tb, "ensure_official_image", lambda _task, _cache: ("test-image", "/app"),
    )
    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "run"]:
            pending = tmp_path / "_pending" / "codex__tb__case" / "native_traces"
            pending.mkdir(parents=True)
            (pending / "codex__tb__case.json").write_text(
                '{"source_completed": false, "model_finished": true, '
                '"result": {"summary": "submitted"}}'
            )
            write_native_verdict(tmp_path, "codex__tb__case")
        return CompletedProcess(command, 0)
    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    args = SimpleNamespace(
        agent="codex", cache_dir=tmp_path, output_dir=tmp_path,
        model="gpt-5.6-luna", reasoning_effort="max",
        max_steps=2, max_model_calls=2, task_timeout_s=3600,
    )
    record = capture.capture_terminal_in_image("case", args)
    assert record["source_completed"] is False
    assert record["source_success"] is True


def test_verifier_exception_preserves_completed_native_trace(monkeypatch, tmp_path):
    task = SimpleNamespace(instruction="Create output")
    stub_native_tb_verifier(monkeypatch, tmp_path)
    monkeypatch.setattr(tb, "load_tasks", lambda _cache: {"case": task})
    monkeypatch.setattr(
        tb, "materialize", lambda _task, workdir, _cache, **_kwargs: workdir.mkdir(),
    )
    monkeypatch.setattr(
        tb, "ensure_official_image", lambda _task, _cache: ("test-image", "/app"),
    )
    def fake_run(command, **kwargs):
        if command[:2] == ["docker", "run"]:
            pending = tmp_path / "_pending" / "tb__case" / "native_traces"
            pending.mkdir(parents=True)
            (pending / "tb__case.json").write_text(
                '{"source_completed": true, "model_finished": true, '
                '"result": {"summary": "submitted"}}'
            )
        return CompletedProcess(command, 0)
    monkeypatch.setattr(capture.subprocess, "run", fake_run)
    args = SimpleNamespace(
        agent="sweagent", cache_dir=tmp_path, output_dir=tmp_path,
        model="gpt-5.6-luna", reasoning_effort="max",
        max_steps=2, max_model_calls=2, task_timeout_s=3600,
    )
    record = capture.capture_terminal_in_image("case", args)
    assert record["source_completed"] is True
    assert record["source_success"] is None
    assert record["verification_error"].startswith("FileNotFoundError:")


def test_swe_image_base_accepts_only_mode_changes(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    source = tmp_path / "source.py"
    source.write_text("print('base')\n")
    subprocess.run(["git", "add", "source.py"], cwd=tmp_path, check=True)
    commit = [
        "git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
        "commit", "-q", "-m", "baseline",
    ]
    subprocess.run(commit, cwd=tmp_path, check=True)
    base = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    source.chmod(0o755)
    subprocess.run(["git", "add", "source.py"], cwd=tmp_path, check=True)
    subprocess.run(commit, cwd=tmp_path, check=True)
    assert capture.image_matches_base_contents(tmp_path, base)[0]
    source.write_text("print('changed')\n")
    subprocess.run(["git", "add", "source.py"], cwd=tmp_path, check=True)
    subprocess.run(commit, cwd=tmp_path, check=True)
    assert not capture.image_matches_base_contents(tmp_path, base)[0]


def test_swe_image_metadata_exception_is_blob_pinned():
    allowed = (
        b"tox.ini",
        b"105a02597ecd2cdf5d4a027a227150377be5f9e6",
        b"90ada082109b1ddee1e4ec4e0b5c09d1828f3670",
    )
    assert allowed in capture.ALLOWED_IMAGE_METADATA_PATCHES
    assert (b"tox.ini", allowed[1], b"0" * 40) not in capture.ALLOWED_IMAGE_METADATA_PATCHES


def test_astropy_image_allows_only_exact_build_pin(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    (tmp_path / ".astropy-root").write_text("")
    config = tmp_path / "pyproject.toml"
    config.write_text('[build-system]\nrequires = ["setuptools", "wheel"]\n')
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    commit = ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
              "commit", "-q", "-m", "image"]
    subprocess.run(commit, cwd=tmp_path, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                          capture_output=True, text=True, check=True).stdout.strip()
    config.write_text('[build-system]\nrequires = ["setuptools==68.0.0", "wheel"]\n')
    config.chmod(0o755)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(commit, cwd=tmp_path, check=True)
    assert capture.image_matches_base_contents(tmp_path, base)[0]
    config.write_text(config.read_text() + "other = true\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(commit, cwd=tmp_path, check=True)
    assert not capture.image_matches_base_contents(tmp_path, base)[0]
