from types import SimpleNamespace

import pytest

from experiments.scripts import check_experiment_environment as check_env


def test_select_tasks_uses_exact_prefixed_subset(monkeypatch, tmp_path):
    monkeypatch.setattr(check_env.swe, "load_tasks", lambda _cache: {"one": object()})
    monkeypatch.setattr(check_env.tb, "load_tasks", lambda _cache: {"two": object()})

    swe_tasks, tb_tasks, selected = check_env.select_tasks(
        cache_dir=tmp_path,
        suite="all",
        task_set="selected",
        requested=("swe:one", "tb:two"),
    )

    assert list(swe_tasks) == ["one"]
    assert list(tb_tasks) == ["two"]
    assert selected == [("swe", "one"), ("tb", "two")]


def test_select_tasks_rejects_unknown_case(monkeypatch, tmp_path):
    monkeypatch.setattr(check_env.swe, "load_tasks", lambda _cache: {})
    monkeypatch.setattr(check_env.tb, "load_tasks", lambda _cache: {})

    with pytest.raises(ValueError, match="unknown task"):
        check_env.select_tasks(
            cache_dir=tmp_path,
            suite="all",
            task_set="selected",
            requested=("swe:missing",),
        )


def test_report_fails_for_missing_or_failed_cases():
    report = check_env.build_report(
        checks=[{"name": "runtime", "ok": True}],
        rows=[{"suite": "swe", "task": "one", "ok": False}],
        expected=2,
    )

    assert report["ok"] is False
    assert report["checked_tasks"] == 1
    assert report["failed_tasks"] == ["swe:one"]


def test_check_task_reports_exact_failed_setup_stage(monkeypatch, tmp_path):
    task = SimpleNamespace(instance_id="one")
    monkeypatch.setattr(check_env, "validate_swe_repo", lambda *_args: (tmp_path, {}))
    monkeypatch.setattr(
        check_env,
        "validate_swe_venv",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("dependency missing")),
    )
    monkeypatch.setattr(check_env, "validate_docker_image", lambda *_args: "image")

    row = check_env.check_task(
        suite="swe",
        name="one",
        cache_dir=tmp_path,
        swe_catalog={"one": task},
        tb_catalog={},
        dependency_cache={},
    )

    assert row["ok"] is False
    assert row["stage"] == "dependencies"
    assert "dependency missing" in row["detail"]


def test_prepare_missing_swe_venv_once_per_shared_key(monkeypatch, tmp_path):
    tasks = {
        "one": SimpleNamespace(instance_id="one"),
        "two": SimpleNamespace(instance_id="two"),
    }
    monkeypatch.setattr(check_env, "validate_swe_repo", lambda *_args: (tmp_path, {"repo": "org/repo", "version": "1"}))
    monkeypatch.setattr(check_env.swe, "_venv_key", lambda *_args: "shared")
    attempts = []

    def validate(task, *_args):
        if not attempts:
            raise FileNotFoundError(task.instance_id)
        return str(tmp_path / "venv")

    monkeypatch.setattr(check_env, "validate_swe_venv", validate)
    monkeypatch.setattr(check_env.swe, "ensure_venv", lambda task, *_args: attempts.append(task.instance_id))
    monkeypatch.setattr(check_env, "validate_docker_image", lambda *_args: "image")
    dependency_cache = {}

    rows = [
        check_env.check_task(
            suite="swe",
            name=name,
            cache_dir=tmp_path,
            swe_catalog=tasks,
            tb_catalog={},
            dependency_cache=dependency_cache,
            python="python",
            prepare_missing=True,
        )
        for name in tasks
    ]

    assert attempts == ["one"]
    assert all(row["ok"] for row in rows)
    assert "(prepared)" in rows[0]["detail"]


def test_check_tb_task_uses_embedded_run_tests_and_compose(monkeypatch, tmp_path):
    task = SimpleNamespace(task_id="two")
    source = tmp_path / "two"
    source.mkdir()
    (source / "task.yaml").write_text("instruction: test\n", encoding="utf-8")
    (source / "run-tests.sh").write_text("#!/bin/sh\npytest\n", encoding="utf-8")
    (source / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(check_env.tb, "task_source", lambda *_args: source)
    monkeypatch.setattr(check_env, "validate_tb_docker", lambda value: str(value / "docker-compose.yaml"))

    row = check_env.check_task(
        suite="tb",
        name="two",
        cache_dir=tmp_path,
        swe_catalog={},
        tb_catalog={"two": task},
        dependency_cache={},
    )

    assert row["ok"] is True
    assert row["stage"] == "complete"
    assert row["dependencies_ok"] is True
    assert row["docker_ok"] is True
