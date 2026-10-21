from concurrent.futures import ThreadPoolExecutor
import subprocess
import sys
import time
from pathlib import Path

from experiments.workloads import swe_bench_suite as swe
from experiments.workloads.swe_bench_suite import (
    REPO_PIP,
    SWETask,
    django_test_labels,
    ensure_repo_dependencies,
    ensure_venv,
    ensure_workspace_venv,
    prepare_workspace_runtime,
    swe_eval_group,
    swe_eval_image,
    task_prompt as swe_task_prompt,
    test_command as swe_test_command,
)


def test_repo_dependency_preflight_skips_repos_without_submodules(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        swe, "_git", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError())
    )
    ensure_repo_dependencies(tmp_path / "repo", tmp_path / "cache")


def test_repo_dependency_preflight_initializes_and_verifies_submodules(
    monkeypatch, tmp_path
):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitmodules").write_text(
        '[submodule "dependency"]\npath = dependency\nurl = https://example/dependency\n',
        encoding="utf-8",
    )
    statuses = iter(["-abc dependency", " abc dependency"])
    monkeypatch.setattr(swe, "_git", lambda *args, **kwargs: next(statuses))
    calls = []
    monkeypatch.setattr(
        swe.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs))
        or subprocess.CompletedProcess(args[0], 0),
    )

    ensure_repo_dependencies(repo, tmp_path / "cache")

    command, kwargs = calls[0]
    assert command[0][-4:] == ["update", "--init", "--recursive", "--checkout"]
    assert kwargs["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert kwargs["timeout"] == 600


def test_repo_dependency_preflight_reports_update_failure(monkeypatch, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".gitmodules").write_text("[submodule \"dependency\"]\n", encoding="utf-8")
    monkeypatch.setattr(swe, "_git", lambda *args, **kwargs: "-abc dependency")
    monkeypatch.setattr(
        swe.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="network unavailable"
        ),
    )

    try:
        ensure_repo_dependencies(repo, tmp_path / "cache")
    except RuntimeError as exc:
        assert "network unavailable" in str(exc)
    else:
        raise AssertionError("submodule update failure must abort dependency preflight")


def test_astropy_agent_dependencies_exclude_incompatible_toolchain_versions():
    dependencies = REPO_PIP["astropy/astropy"]

    assert "numpy<2" in dependencies
    assert "pytest<8" in dependencies
    assert "extension-helpers" in dependencies
    assert "setuptools<70" in dependencies
    assert "Cython<3" in dependencies
    assert "PyYAML" in dependencies


def test_workspace_runtime_builds_only_astropy(monkeypatch, tmp_path):
    calls = []
    venv = tmp_path / ".venv"
    python = venv / "bin" / "python"
    venv.mkdir(parents=True)
    (venv / ".agenttx_dependency_paths").write_text(
        "/cached/site-packages\n", encoding="utf-8"
    )
    monkeypatch.setattr(swe.subprocess, "run", lambda *args, **kwargs: calls.append((args, kwargs)) or subprocess.CompletedProcess(args[0], 0))

    prepare_workspace_runtime(tmp_path, {"repo": "django/django"}, str(python))
    assert calls == []

    prepare_workspace_runtime(tmp_path, {"repo": "astropy/astropy"}, str(python))
    command, kwargs = calls[0]
    assert command[0] == [str(python), "setup.py", "build_ext", "--inplace"]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"]["PYTHONPATH"].startswith("/cached/site-packages")


def test_ensure_venv_serializes_shared_builders_and_uses_python_m_pip(
    monkeypatch, tmp_path
):
    task = SWETask(
        instance_id="django__django-10914",
        scale="short",
        doc_lines=8,
        doc_specs=(("recovery_notes/design.md", "design"),),
        pythonpath="auto",
        max_turns=8,
        faulty_relpath="django/conf/global_settings.py",
    )
    instance = {"repo": "django/django", "version": "3.2"}
    calls = []

    def fake_run(command, check):
        calls.append(list(command))
        if command[1:3] == ["-m", "venv"]:
            time.sleep(0.05)
            py = Path(command[-1]) / "bin" / "python"
            py.parent.mkdir(parents=True, exist_ok=True)
            py.write_text("", encoding="utf-8")

    monkeypatch.setattr(swe.subprocess, "run", fake_run)
    args = (task, sys.executable, tmp_path, instance)
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(lambda _: ensure_venv(*args), range(4)))

    assert len(set(results)) == 1
    assert sum(command[1:3] == ["-m", "venv"] for command in calls) == 1
    pip_calls = [command for command in calls if command[1:3] == ["-m", "pip"]]
    assert len(pip_calls) == 2
    assert all(command[0].endswith("/bin/python") for command in pip_calls)


def test_eval_image_pull_retries_transient_failure(monkeypatch, tmp_path):
    task_id = "django__django-12708"
    image = swe_eval_image(task_id)
    responses = [
        subprocess.CompletedProcess([], 1),
        subprocess.CompletedProcess([], 1),
        subprocess.CompletedProcess([], 1, stdout="", stderr="connection reset"),
        subprocess.CompletedProcess([], 0, stdout="pulled", stderr=""),
    ]
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        return responses.pop(0)

    monkeypatch.setenv("AGENTTX_DOCKER_PULL_LOCK", str(tmp_path / "pull.lock"))
    monkeypatch.setattr(swe.subprocess, "run", fake_run)
    monkeypatch.setattr(swe.time, "sleep", lambda _seconds: None)

    assert swe.ensure_eval_image(task_id) == image
    assert sum(command[:2] == ["docker", "pull"] for command in calls) == 2


def test_eval_image_pull_uses_mirrors_without_proxy_and_retags(monkeypatch, tmp_path):
    task_id = "astropy__astropy-12907"
    image = swe_eval_image(task_id)
    mirror_image = f"mirror.example/{image}"
    responses = [
        subprocess.CompletedProcess([], 1),
        subprocess.CompletedProcess([], 1),
        subprocess.CompletedProcess([], 0, stdout="pulled", stderr=""),
        subprocess.CompletedProcess([], 0),
    ]
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return responses.pop(0)

    monkeypatch.setenv("AGENTTX_DOCKER_PULL_LOCK", str(tmp_path / "pull.lock"))
    monkeypatch.setenv("AGENTTX_DOCKER_MIRROR_PREFIXES", "mirror.example")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:17890")
    monkeypatch.setattr(swe.subprocess, "run", fake_run)

    assert swe.ensure_eval_image(task_id) == image
    pull_command, pull_kwargs = calls[2]
    assert pull_command == ["docker", "pull", mirror_image]
    assert "HTTPS_PROXY" not in pull_kwargs["env"]
    assert pull_kwargs["env"]["NO_PROXY"] == "*"
    assert calls[3][0] == ["docker", "tag", mirror_image, image]


def test_eval_images_are_kept_by_default(monkeypatch):
    calls = []
    monkeypatch.delenv("AGENTTX_KEEP_EVAL_IMAGES", raising=False)
    monkeypatch.setattr(swe.subprocess, "run", lambda *args, **kwargs: calls.append(args))

    swe.remove_eval_image("django__django-12708")
    swe.prune_unused_eval_layers()

    assert calls == []


def test_eval_image_cleanup_requires_explicit_opt_in(monkeypatch):
    calls = []
    monkeypatch.setenv("AGENTTX_KEEP_EVAL_IMAGES", "0")
    monkeypatch.setattr(swe, "docker_available", lambda: True)
    monkeypatch.setattr(
        swe.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )

    swe.remove_eval_image("django__django-12708")
    swe.prune_unused_eval_layers()

    assert calls == [
        ["docker", "rmi", "-f", swe.swe_eval_image("django__django-12708")],
        ["docker", "image", "prune", "-f"],
    ]


def test_docker_verifier_reports_image_pull_failure(monkeypatch, tmp_path):
    interpreter=tmp_path/'.venv/bin/python'
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text('live interpreter')
    temporary=tmp_path/'.tmp/active-command'
    temporary.parent.mkdir()
    temporary.write_text('live command state')
    task = SWETask(
        instance_id="django__django-12708",
        scale="short",
        doc_lines=8,
        doc_specs=(("recovery_notes/design.md", "design"),),
        pythonpath="auto",
        max_turns=8,
        faulty_relpath="django/db/models/base.py",
    )
    monkeypatch.setattr(
        swe,
        "ensure_eval_image",
        lambda _instance_id: (_ for _ in ()).throw(RuntimeError("pull failed")),
    )
    monkeypatch.setattr(swe.subprocess, "run", lambda *args, **kwargs: None)

    verdict = swe._verify_tests_docker(
        tmp_path,
        task,
        {"repo": "django/django", "FAIL_TO_PASS": []},
    )

    assert verdict["tests_rc"] == "error"
    assert verdict["verifier_stderr"] == "pull failed"
    assert interpreter.read_text()=='live interpreter'
    assert temporary.read_text()=='live command state'


def test_django_paren_ids_become_runtests_labels():
    labels = django_test_labels(
        ["test_override_file_upload_permissions (test_utils.tests.OverrideSettingsTests)"]
    )
    assert labels == [
        "test_utils.tests.OverrideSettingsTests.test_override_file_upload_permissions"
    ]


def test_django_command_uses_runtests_and_quotes():
    task = SWETask(
        instance_id="django__django-10914",
        scale="short",
        doc_lines=8,
        doc_specs=(("recovery_notes/design.md", "design"),),
        pythonpath="auto",
        max_turns=8,
        faulty_relpath="django/conf/global_settings.py",
    )
    instance = {
        "repo": "django/django",
        "FAIL_TO_PASS": [
            "test_override_file_upload_permissions (test_utils.tests.OverrideSettingsTests)"
        ],
    }
    cmd = swe_test_command(task, instance, "/tmp/python")
    assert "tests/runtests.py" in cmd
    assert "pytest" not in cmd
    assert "test_utils.tests.OverrideSettingsTests.test_override_file_upload_permissions" in cmd
    assert "(" not in cmd.split("runtests.py", 1)[1]


def test_pytest_nodes_are_quoted():
    task = SWETask(
        instance_id="astropy__astropy-12907",
        scale="short",
        doc_lines=8,
        doc_specs=(("recovery_notes/design.md", "design"),),
        pythonpath="auto",
        max_turns=8,
        faulty_relpath="astropy/modeling/separable.py",
    )
    instance = {
        "repo": "astropy/astropy",
        "FAIL_TO_PASS": [
            "astropy/modeling/tests/test_separable.py::test_separable[compound_model6-result6]"
        ],
    }
    cmd = swe_test_command(task, instance, "/tmp/python")
    assert "-m pytest" in cmd
    assert "LD_LIBRARY_PATH=/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" in cmd
    assert "'astropy/modeling/tests/test_separable.py::test_separable[compound_model6-result6]'" in cmd


def test_eval_image_rewrites_double_underscore():
    assert swe_eval_image("astropy__astropy-12907") == (
        "swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest"
    )
    assert swe_eval_image("django__django-10914") == (
        "swebench/sweb.eval.x86_64.django_1776_django-10914:latest"
    )


def test_django_command_uses_official_sqlite_settings():
    task = SWETask(
        instance_id="django__django-10914",
        scale="short",
        doc_lines=8,
        doc_specs=(("recovery_notes/design.md", "design"),),
        pythonpath="auto",
        max_turns=8,
        faulty_relpath="django/conf/global_settings.py",
    )
    instance = {
        "repo": "django/django",
        "FAIL_TO_PASS": [
            "test_override_file_upload_permissions (test_utils.tests.OverrideSettingsTests)"
        ],
    }
    cmd = swe_test_command(task, instance, "/tmp/python")
    assert "--settings=test_sqlite" in cmd
    assert "--parallel 1" in cmd


def test_eval_group_is_repo_not_instance():
    assert swe_eval_group("django__django-10914") == "django__django"
    assert swe_eval_group("astropy__astropy-12907") == "astropy__astropy"
    assert swe_eval_group("pytest-dev__pytest-8906") == "pytest-dev__pytest"
    assert swe_eval_group("scikit-learn__scikit-learn-25570") == "scikit-learn__scikit-learn"


def test_django_skips_docstring_ids():
    labels = django_test_labels(
        [
            "test_clear (sessions_tests.tests.CookieSessionTests)",
            "Strings of length 8 and up are accepted and stored.",
        ]
    )
    assert labels == ["sessions_tests.tests.CookieSessionTests.test_clear"]


def test_django_does_not_double_fully_qualified_path():
    labels = django_test_labels(
        [
            "test_alter_alter_field (migrations.test_optimizer.OptimizerTests.test_alter_alter_field)"
        ]
    )
    assert labels == ["migrations.test_optimizer.OptimizerTests.test_alter_alter_field"]


def test_django_docstring_only_uses_test_patch_module():
    labels = django_test_labels(
        ["@method_decorator preserves wrapper assignments."],
        instance={"test_patch": "+++ b/tests/decorators/tests.py\n"},
    )
    assert labels == ["decorators"]


def test_workspace_venv_stays_inside_workdir(tmp_path):
    import subprocess

    py = ensure_workspace_venv(tmp_path, sys.executable)
    assert str(py).startswith(str(tmp_path))
    assert Path(py).exists()
    prefix = subprocess.check_output(
        [py, "-c", "import sys; print(sys.prefix)"], text=True
    ).strip()
    assert prefix.startswith(str(tmp_path))
    cmd = swe_test_command(
        SWETask(
            instance_id="astropy__astropy-12907",
            scale="short",
            doc_lines=8,
            doc_specs=(("recovery_notes/design.md", "design"),),
            pythonpath="auto",
            max_turns=8,
            faulty_relpath="astropy/modeling/separable.py",
        ),
        {
            "repo": "astropy/astropy",
            "FAIL_TO_PASS": [
                "astropy/modeling/tests/test_separable.py::test_separable[compound_model6-result6]"
            ],
        },
        py,
    )
    assert py in cmd
    prompt = swe_task_prompt(
        SWETask(
            instance_id="astropy__astropy-12907",
            scale="short",
            doc_lines=8,
            doc_specs=(("recovery_notes/design.md", "design"),),
            pythonpath="auto",
            max_turns=8,
            faulty_relpath="astropy/modeling/separable.py",
        ),
        {
            "repo": "astropy/astropy",
            "base_commit": "deadbeef",
            "problem_statement": "fix it",
            "FAIL_TO_PASS": [
                "astropy/modeling/tests/test_separable.py::test_separable[compound_model6-result6]"
            ],
        },
        py,
    )
    assert py in prompt
    assert "host conda" in prompt
    assert "causal recovery policy retained" in prompt
    assert "Do not open, inspect, verify, or rewrite anything under `recovery_notes/`" in prompt
    abort_prompt = swe_task_prompt(
        SWETask(
            instance_id="astropy__astropy-12907",
            scale="short",
            doc_lines=8,
            doc_specs=(("recovery_notes/design.md", "design"),),
            pythonpath="auto",
            max_turns=8,
            faulty_relpath="astropy/modeling/separable.py",
        ),
        {
            "repo": "astropy/astropy",
            "base_commit": "deadbeef",
            "problem_statement": "fix it",
            "FAIL_TO_PASS": [
                "astropy/modeling/tests/test_separable.py::test_separable[compound_model6-result6]"
            ],
        },
        py,
        mode="whole_branch_abort",
    )
    assert "whole-branch abort policy discarded" in abort_prompt
    assert "was lost" in abort_prompt
    assert "causal recovery policy retained" not in abort_prompt


def test_workspace_venv_imports_cached_task_dependencies(tmp_path):
    source = tmp_path / "cached"
    subprocess.run([sys.executable, "-m", "venv", str(source)], check=True)
    source_python = source / "bin" / "python"
    source_site = subprocess.check_output(
        [
            source_python,
            "-c",
            "import site; print(site.getsitepackages()[0])",
        ],
        text=True,
    ).strip()
    Path(source_site, "agenttx_seed_probe.py").write_text(
        "VALUE = 'available'\n", encoding="utf-8"
    )

    workspace_python = ensure_workspace_venv(tmp_path / "work", str(source_python))

    imported = subprocess.check_output(
        [
            workspace_python,
            "-c",
            "import collections, agenttx_seed_probe; "
            "print(agenttx_seed_probe.VALUE, collections.MutableSequence.__name__)",
        ],
        text=True,
    ).strip()
    assert imported == "available MutableSequence"
