import sys
from pathlib import Path

from experiments.workloads.swe_bench_suite import (
    SWETask,
    django_test_labels,
    ensure_workspace_venv,
    swe_eval_group,
    swe_eval_image,
    task_prompt as swe_task_prompt,
    test_command as swe_test_command,
)


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
