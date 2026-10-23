from pathlib import Path
from dataclasses import replace

import pytest

from experiments.scripts.bench_official_tasks import (
    FAULT_MODES,
    MANIFEST_MODES,
    POLICY_MODES,
    RANDOMIZED_CRASH_MODES,
    _DirectExternalAgent,
    _apply_blind_policy,
    _auto_localize_policy,
    _apply_policy,
    _completed_attempt,
    _terminal_attempt,
    _load_prior_rows,
    _result_exit_code,
    _retained_artifacts_unchanged,
    _seed_direct_crash_state,
    _verifier_environment_error,
    official_group,
    summarize,
    token_summaries,
    write_outputs,
)
from experiments.workloads import terminal_bench_suite as tb
from experiments.workloads import swe_bench_suite as swe
from experiments.workloads.recovery_inject import (
    DocSpec,
    build_recovery_manifest,
    document_content,
    recovery_manifest_json,
    render_recovery_manifest_prompt,
    retained_artifact_access,
)
from agenttx.ledger import Effect, EffectKind, Ledger


def test_policy_modes_include_the_ideal_oracle_control():
    assert POLICY_MODES == (
        "causal",
        "temporal_checkpoint",
        "whole_branch_abort",
        "ideal_oracle",
    )


def test_native_harness_uses_private_credentials_and_explicit_checkout(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace
    from experiments.scripts import bench_official_tasks as bench

    captured = {}
    def profile(config, auth, **kwargs):
        captured.update(config=config, auth=auth)
        return "profile-without-key-in-config"
    def harness(**kwargs):
        captured.update(kwargs)
        return "native-harness"
    monkeypatch.setitem(sys.modules, "experiments.scripts.sweagent_native_workload", SimpleNamespace(
        DEFAULT_CODEX_CONFIG=Path("/absent/global-config"),
        DEFAULT_CODEX_AUTH=Path("/absent/global-auth"),
        DEFAULT_SWE_AGENT_ROOT=Path("/absent/global-checkout"),
        load_codex_gpt_profile=profile, SWEAgentNativeWorkloadHarness=harness))
    monkeypatch.setenv("AGENTTX_SWE_PROFILE", str(tmp_path / "private-swe.toml"))
    monkeypatch.setenv("CODEX_CONFIG_FILE", str(tmp_path / "codex.toml"))
    monkeypatch.setenv("CODEX_AUTH_FILE", str(tmp_path / "private-auth.json"))
    result = bench._create_workload_harness("sweagent", harness_root=tmp_path / "SWE-agent",
        model="gpt-5.6-luna", harness_command=None, harness_timeout_s=120,
        output_dir=tmp_path, max_turns=8, reasoning_effort="max")
    assert result == "native-harness"
    assert captured["config"] == tmp_path / "private-swe.toml"
    assert captured["auth"] == tmp_path / "private-auth.json"
    assert captured["config_path"] == tmp_path / "SWE-agent/config/bash_only.yaml"
    assert captured["swe_agent_root"] == tmp_path / "SWE-agent"


def test_terminal_formal_runner_rejects_host_projected_agent(monkeypatch, capsys):
    from experiments.scripts import bench_official_tasks as bench

    monkeypatch.setattr(bench, "load_provider_env", lambda _root: None)
    monkeypatch.setattr(bench, "load_llm_env", lambda: None)
    monkeypatch.setattr(tb, "load_tasks", lambda _cache: {"case": tb.SHORT})

    assert bench.main(["--suite", "tb", "--modes", "clean_recovery"]) == 2
    assert "image-native agent" in capsys.readouterr().err


def test_terminal_docker_commands_ignore_inherited_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:17897")
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:17897")
    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(tb.subprocess, "run", fake_run)
    tb._docker_run(["docker", "image", "inspect", "example"])

    assert "HTTPS_PROXY" not in captured["env"]
    assert "http_proxy" not in captured["env"]
    assert captured["env"]["NO_PROXY"] == "*"


@pytest.mark.parametrize("task_id", sorted(tb._CACHED_DOWNLOADS))
def test_terminal_cached_downloads_replace_only_known_instructions(tmp_path, monkeypatch, task_id):
    source = tmp_path / task_id
    source.mkdir()
    downloads = tb._CACHED_DOWNLOADS[task_id]
    original = "FROM ghcr.io/example/base:latest\n" + "\n".join(
        instruction for instruction, _, _, _ in downloads
    ) + "\nRUN true\n"
    if task_id == "leelachess0-pytorch-conversion":
        original += (
            "RUN pip install torch==2.8.0 torchvision==0.23.0 "
            "--index-url https://download.pytorch.org/whl/cpu\n"
        )
    (source / "Dockerfile").write_text(original)
    cache = tmp_path / "assets"
    cache.mkdir()
    for _, filename, _, _ in downloads:
        (cache / filename).write_bytes(b"fixture")
    monkeypatch.setenv("AGENTTX_TB_ASSET_CACHE", str(cache))
    monkeypatch.setattr(tb, "_verify_local_asset", lambda path, digest: None)

    candidates = tb._dockerfile_candidates(source)
    try:
        assert all(local_assets for _, _, local_assets in candidates)
        for dockerfile, _, _ in candidates:
            text = dockerfile.read_text()
            assert "RUN true" in text
            for instruction, filename, destination, _ in downloads:
                assert instruction not in text
                assert f"COPY --from=agenttx-assets {filename} {destination}" in text
    finally:
        for dockerfile, _, _ in candidates:
            dockerfile.unlink()


def test_terminal_cached_download_rejects_bad_asset(tmp_path):
    source = tmp_path / "query-optimize"
    source.mkdir()
    instruction, filename, _, _ = tb._CACHED_DOWNLOADS[source.name][0]
    cache = tmp_path / "assets"
    cache.mkdir()
    (cache / filename).write_bytes(b"wrong bytes")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        tb._cached_download_dockerfile(source, instruction, cache)


def test_terminal_cached_models_replace_only_download_step(tmp_path, monkeypatch):
    source = tmp_path / "llm-spec-decoding"
    source.mkdir()
    cache = tmp_path / "assets"
    for model, files in tb._SMOLLM2_ASSETS.items():
        directory = cache / model
        directory.mkdir(parents=True)
        for filename in files:
            (directory / filename).write_bytes(b"fixture")
    monkeypatch.setattr(tb, "_verify_local_asset", lambda path, digest: None)
    original = (
        "FROM example\nRUN echo keep-me > /tmp/download_models.py\n"
        "RUN python /tmp/download_models.py && rm /tmp/download_models.py\n"
    )

    rewritten, cached = tb._cached_model_dockerfile(source, original, cache)
    assert cached
    assert "RUN echo keep-me" in rewritten
    assert "COPY --from=agenttx-assets smollm2-360m/" in rewritten
    assert "COPY --from=agenttx-assets smollm2-135m/" in rewritten
    assert "RUN python /tmp/download_models.py" not in rewritten


@pytest.mark.parametrize("task_id, anchor", [
    ("mnist-learning-fix", "COPY tests/model.py ./"),
    ("reshard-c4-data", "COPY setup.py ./"),
    ("word2vec-from-scratch", "RUN python -c \"import os; from datasets import load_dataset; ds=load_dataset('wikitext','wikitext-2-v1')\""),
])
def test_terminal_cached_datasets_preserve_official_input_files(tmp_path, monkeypatch, task_id, anchor):
    source = tmp_path / task_id
    source.mkdir()
    cache = tmp_path / "assets"
    for filename in tb._DATASET_ASSETS[task_id]:
        path = cache / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"fixture")
    monkeypatch.setattr(tb, "_verify_local_asset", lambda path, digest: None)

    rewritten, cached = tb._cached_dataset_dockerfile(source, "FROM example\n" + anchor, cache)
    assert cached
    assert "COPY --from=agenttx-assets" in rewritten
    if task_id == "mnist-learning-fix":
        assert "hf://datasets/ylecun/mnist/" in rewritten
        assert "/app/mnist/" in rewritten
    elif task_id == "reshard-c4-data":
        assert "c4-train.json.gz" in rewritten
        assert 's|"allenai/c4"|"json"|' in rewritten
    else:
        assert "load_dataset('parquet'" in rewritten
        assert "wikitext-train.parquet" in rewritten


def test_terminal_bullseye_build_uses_pinned_debian_snapshot(tmp_path):
    source = tmp_path / "qemu-startup"
    source.mkdir()
    (source / "Dockerfile").write_text(
        "FROM debian:bullseye-slim\nRUN apt-get update\n", encoding="utf-8"
    )
    candidates = tb._dockerfile_candidates(source)
    try:
        dockerfile, label, _ = candidates[0]
        assert label == "debian-snapshot-20260831"
        assert "snapshot.debian.org/archive/debian-security/20260831T210000Z/" in dockerfile.read_text()
        assert (source / "Dockerfile").read_text().startswith("FROM debian:bullseye-slim")
    finally:
        for dockerfile, _, _ in candidates:
            if dockerfile is not None:
                dockerfile.unlink()


def test_terminal_build_error_keeps_actual_failure_amid_stack_trace():
    output = "\n".join(
        ["#0 building", *[f"#1 step {index}" for index in range(100)],
         "#1 ERROR: package not found", *["buildkit stack" for _ in range(100)]]
    )
    assert "package not found" in tb._build_failure_detail(output, "")
    output = "\n".join(["build step", "#12 requests.exceptions.ConnectionError: Network is unreachable", *["go stack"] * 80])
    assert "Network is unreachable" in tb._build_failure_detail(output, "")


def test_terminal_build_passes_local_asset_context(tmp_path, monkeypatch):
    source = tmp_path / "query-optimize"
    source.mkdir()
    instruction, filename, _, _ = tb._CACHED_DOWNLOADS[source.name][0]
    (source / "Dockerfile").write_text("FROM ghcr.io/example/base:latest\n" + instruction)
    cache = tmp_path / "assets"
    cache.mkdir()
    (cache / filename).write_bytes(b"fixture")
    monkeypatch.setenv("AGENTTX_TB_ASSET_CACHE", str(cache))
    monkeypatch.setattr(tb, "_verify_local_asset", lambda path, digest: None)
    monkeypatch.setattr(tb, "task_source", lambda cache_root, task: source)
    commands = []

    def fake_docker_run(command, *, timeout=1800):
        commands.append(command)
        if command[:3] == ["docker", "image", "inspect"] and "--format" not in command:
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        return type("Result", (), {"returncode": 0, "stdout": "/app\n", "stderr": ""})()

    monkeypatch.setattr(tb, "_docker_run", fake_docker_run)
    task = replace(tb.SHORT, task_id="query-optimize")
    tb.ensure_official_image(task, tmp_path)

    build = next(command for command in commands if command[:2] == ["docker", "build"])
    assert ["--build-context", f"agenttx-assets={cache}"] == build[6:8]


def test_terminal_build_uses_compose_client_context(tmp_path, monkeypatch):
    source = tmp_path / "nested-client"
    client = source / "client"
    client.mkdir(parents=True)
    (source / "docker-compose.yaml").write_text(
        "services:\n  client:\n    build:\n      context: client\n      dockerfile: Dockerfile\n",
        encoding="utf-8",
    )
    (client / "Dockerfile").write_text("FROM alpine\nWORKDIR /app\n", encoding="utf-8")
    monkeypatch.setattr(tb, "task_source", lambda _cache, _task: source)
    commands = []

    def fake_docker_run(command, *, timeout=1800):
        commands.append(command)
        if command[:3] == ["docker", "image", "inspect"] and "--format" not in command:
            return type("Result", (), {"returncode": 1, "stdout": "", "stderr": ""})()
        return type("Result", (), {"returncode": 0, "stdout": "/app\n", "stderr": ""})()

    monkeypatch.setattr(tb, "_docker_run", fake_docker_run)
    task = replace(tb.SHORT, task_id=source.name)
    tb.ensure_official_image(task, tmp_path)

    build = next(command for command in commands if command[:2] == ["docker", "build"])
    assert build[-1] == str(client.resolve())


def test_terminal_image_accepts_non_app_workdir(tmp_path, monkeypatch):
    source = tmp_path / "special-workdir"
    source.mkdir()
    (source / "Dockerfile").write_text("FROM alpine\nWORKDIR /workspace\n", encoding="utf-8")
    monkeypatch.setattr(tb, "task_source", lambda _cache, _task: source)

    def fake_docker_run(command, *, timeout=1800):
        return type("Result", (), {
            "returncode": 0,
            "stdout": "/workspace\n" if "--format" in command else "",
            "stderr": "",
        })()

    monkeypatch.setattr(tb, "_docker_run", fake_docker_run)
    task = replace(tb.SHORT, task_id=source.name)
    assert tb.ensure_official_image(task, tmp_path)[1] == "/workspace"


def test_terminal_verifier_accepts_embedded_tests_without_tests_dir(tmp_path, monkeypatch):
    source = tmp_path / "embedded-tests"
    source.mkdir()
    (source / "run-tests.sh").write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    commands = []

    def fake_docker_run(command, *, timeout=1800):
        commands.append(command)
        return type("Result", (), {
            "returncode": 0,
            "stdout": "container-id\n" if command[:2] == ["docker", "create"] else "",
            "stderr": "",
        })()

    monkeypatch.setattr(tb, "ensure_official_image", lambda *_: ("image", "/app"))
    monkeypatch.setattr(tb, "task_source", lambda *_: source)
    monkeypatch.setattr(tb, "_docker_run", fake_docker_run)
    tb._verify_in_official_container(workspace, tb.SHORT, tmp_path)

    copied = [command[2] for command in commands if command[:2] == ["docker", "cp"]]
    assert not any(item.endswith("/tests/.") for item in copied)


def test_terminal_verifier_timeout_output_is_json_safe(tmp_path, monkeypatch):
    source = tmp_path / "timeout-case"
    source.mkdir()
    (source / "run-tests.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(tb, "ensure_official_image", lambda *_: ("image", "/app"))
    monkeypatch.setattr(tb, "task_source", lambda *_: source)

    def fake_docker_run(command, **_kwargs):
        if command[:3] == ["docker", "exec", "container-id"] and command[-1] == "/tmp/agenttx-run-tests.sh":
            raise tb.subprocess.TimeoutExpired(command, 3600, output=b"partial", stderr=b"waiting")
        return type("Result", (), {
            "returncode": 0,
            "stdout": "container-id\n" if command[:2] == ["docker", "create"] else "",
            "stderr": "",
        })()

    monkeypatch.setattr(tb, "_docker_run", fake_docker_run)
    result = tb._verify_in_official_container(workspace, tb.SHORT, tmp_path)
    assert result.returncode == "timeout"
    assert result.stdout == "partial"
    assert result.stderr == "waiting"


def test_no_rollback_uses_the_same_strict_manifest_contract():
    from experiments.scripts.bench_official_tasks import STRICT_MANIFEST_MODES

    assert STRICT_MANIFEST_MODES == MANIFEST_MODES
    assert "crash_no_rollback" in STRICT_MANIFEST_MODES


def test_ledger_exposes_active_uncommitted_steps():
    ledger = Ledger()
    for index in range(3):
        ledger.add_step("write_file", [Effect(f"file-{index}", EffectKind.WRITE)])
    ledger.committed_frontier = 0
    ledger.steps[0].status = "committed"
    ledger.steps[2].status = "rolled_back"

    assert [step.step_id for step in ledger.active_uncommitted()] == [1]


def test_swe_fault_injection_reads_the_transaction_visible_file():
    class Harness:
        def call_tool(self, name, args):
            assert name == "read_file"
            assert args == {"path": "pkg/core.py"}
            return type(
                "Record",
                (),
                {"returncode": 0, "stdout": "# committed reference\nVALUE = 2\n"},
            )()

    agent = type("Agent", (), {"harness": Harness()})()
    content = swe._faulty_content(agent, "pkg/core.py")

    assert "AGENTTX_INJECTED_FAULT" in content
    assert "# committed reference\nVALUE = 2\n" in content


def test_all_fault_modes_share_the_randomized_crash_plan():
    assert RANDOMIZED_CRASH_MODES == FAULT_MODES
    assert RANDOMIZED_CRASH_MODES == {
        "crash_direct",
        "crash_no_rollback",
        "causal",
        "temporal_checkpoint",
        "whole_branch_abort",
        "ideal_oracle",
    }


def test_ideal_oracle_uses_registered_truth_without_captured_graph():
    class Tx:
        def __init__(self):
            self.calls = []

        def rollback_exact(self, targets):
            self.calls.append(targets)
            return targets

        def rollback_causal(self, root):
            raise AssertionError("oracle must not use captured causal edges")

    tx = Tx()
    agent = type("Agent", (), {"harness": type("Harness", (), {"tx": tx})()})()
    assert _apply_policy(agent, "ideal_oracle", 7, oracle_targets=[7, 9]) == [7, 9]
    assert tx.calls == [[7, 9]]
    with pytest.raises(ValueError, match="registered truth"):
        _apply_policy(agent, "ideal_oracle", 7)
    with pytest.raises(ValueError, match="registered truth"):
        _apply_policy(agent, "ideal_oracle", 7, oracle_targets=[9])


def test_phase_timing_preserves_failed_call_cost_and_error(monkeypatch):
    from experiments.scripts import bench_official_tasks as bench

    ticks = iter([1.0, 2.5])
    monkeypatch.setattr(bench.time, "perf_counter", lambda: next(ticks))
    def fail():
        raise RuntimeError("failed verifier")
    timings = {}
    with pytest.raises(RuntimeError, match="failed verifier"):
        bench._timed_call(timings, "official_grade_wall_s", fail)
    assert timings == {"official_grade_wall_s": 1.5}


def test_multicase_smoke_covers_both_suites_and_three_scales():
    from experiments.scripts.run_codex_multicase_smoke import SMOKE_MODES, SMOKE_TASKS
    from experiments.workloads import swe_bench_suite as swe
    from experiments.workloads import terminal_bench_suite as tb

    assert len(SMOKE_MODES) == 7
    assert set(SMOKE_MODES) == {
        "no_fault",
        "crash_direct",
        "clean_recovery",
        "crash_no_rollback",
        "causal",
        "temporal_checkpoint",
        "whole_branch_abort",
    }
    assert SMOKE_TASKS[:3] == (
        "tb:hello-world",
        "tb:csv-to-parquet",
        "tb:log-summary",
    )
    assert {task.split(":", 1)[0] for task in SMOKE_TASKS} == {"tb", "swe"}
    assert {task.scale for task in swe.TASKS.values()} == {"short", "medium", "long"}
    assert {task.scale for task in tb.TASKS.values()} == {"short", "medium", "long"}


def test_live_success_requires_nonzero_model_usage():
    source = (
        Path(__file__).resolve().parents[1]
        / "experiments/scripts/bench_official_tasks.py"
    ).read_text(encoding="utf-8")
    assert "and (oracle or total_tokens > 0)" in source


def test_coarse_timeout_is_terminal_but_causal_timeout_is_retryable():
    base = {
        "finished": False,
        "finish_called": False,
        "environment_error": True,
        "harness_returncode": 124,
        "error": "",
    }

    assert _terminal_attempt({**base, "mode": "temporal_checkpoint"})
    assert _terminal_attempt({**base, "mode": "whole_branch_abort"})
    assert not _terminal_attempt({**base, "mode": "causal"})


def test_apply_policy_rejects_crab_modes():
    class Tx:
        ledger = type(
            "Ledger",
            (),
            {
                "active_uncommitted": lambda self: [
                    type("Step", (), {"step_id": 0})()
                ]
            },
        )()

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


def test_direct_external_agent_bypasses_transaction_runner(tmp_path):
    sentinel = object()

    class Adapter:
        def run(self, task, workdir):
            assert task == "task"
            assert workdir == tmp_path
            return sentinel

        def run_in_transaction(self, *args, **kwargs):
            raise AssertionError("direct controls must not enter AgentTX")

    agent = _DirectExternalAgent(tmp_path, Adapter())
    assert agent.run("task") is sentinel


def test_direct_crash_seed_materializes_state_without_agenttx(tmp_path, monkeypatch):
    task = type(
        "Task",
        (),
        {
            "task_id": "direct-crash",
            "faulty_relpath": "src/fault.py",
            "faulty_content": "raise RuntimeError('injected')\n",
            "docs": lambda self: (DocSpec("recovery_notes/note.md", "note", 2),),
        },
    )()
    monkeypatch.setattr(
        "experiments.scripts.bench_official_tasks.tb.test_command",
        lambda python, selected: "exit 1",
    )

    injected = _seed_direct_crash_state(
        suite="tb", workdir=tmp_path, task=task, instance=None, python="python3"
    )

    assert injected["runtime"] == "bare_direct"
    assert injected["tests_failed"] is True
    assert (tmp_path / "src/fault.py").read_text() == task.faulty_content
    assert (tmp_path / "recovery_build/derived.txt").read_text() == task.faulty_content
    assert (tmp_path / "recovery_notes/note.md").is_file()


def test_direct_crash_seed_uses_the_shared_randomized_document_split(tmp_path, monkeypatch):
    docs = tuple(
        DocSpec(f"recovery_notes/note-{index}.md", f"note{index}", 2)
        for index in range(3)
    )
    task = type(
        "Task",
        (),
        {
            "task_id": "direct-random-crash",
            "faulty_relpath": "src/fault.py",
            "faulty_content": "raise RuntimeError('injected')\n",
            "docs": lambda self: docs,
        },
    )()
    monkeypatch.setattr(
        "experiments.scripts.bench_official_tasks.tb.test_command",
        lambda python, selected: "exit 1",
    )

    injected = _seed_direct_crash_state(
        suite="tb",
        workdir=tmp_path,
        task=task,
        instance=None,
        python="python3",
        docs=docs,
        fault_after_docs=1,
    )

    assert injected["docs_before"] == [docs[0].path]
    assert injected["docs_after"] == [docs[1].path, docs[2].path]


def test_terminal_bench_injection_forwards_randomized_document_split(monkeypatch):
    docs = tuple(
        DocSpec(f"recovery_notes/random-{index}.md", f"random{index}", 2)
        for index in range(4)
    )
    task = type(
        "Task",
        (),
        {
            "task_id": "tb-random-crash",
            "faulty_relpath": "result.txt",
            "faulty_content": "AGENTTX injected recovery producer\n",
            "docs": lambda self: docs[:2],
        },
    )()
    captured = {}

    def fake_inject(agent, **kwargs):
        captured.update(kwargs)
        return {"root_step": 2}

    monkeypatch.setattr(tb, "inject_recovery_dag", fake_inject)
    monkeypatch.setattr(tb, "test_command", lambda python, selected: "exit 1")

    tb.inject_task_trajectory(
        object(), task, "python3", docs=docs, fault_after_docs=2
    )

    assert captured["docs"] == docs
    assert captured["fault_after_docs"] == 2
    assert "AGENTTX injected recovery producer" in captured["test_cmd"]
    assert "exit 1" not in captured["test_cmd"]


def test_auto_localize_uses_real_failed_verifier_without_injected_path(tmp_path):
    observed = {}

    class Step:
        returncode = 1
        step_id = 7
        parents = []

    class Plan:
        verified = True
        root_step_ids = (3,)
        validation = "verified"
        confidence = 1.0

    class Tx:
        history = [
            type(
                "Record",
                (),
                {
                    "step_id": 9,
                    "argv": ["python3", "-m", "pytest", "-q"],
                    "parents": [3],
                },
            )()
        ]

        ledger = type(
            "Ledger",
            (),
            {"steps": [type("LedgerStep", (), {"parents": set()})() for _ in range(8)]},
        )()

        def run_verifier(self, command, **kwargs):
            observed["command"] = command
            observed["kwargs"] = kwargs
            return Step()

        def plan_recovery(self, failed, **kwargs):
            return Plan()

        def apply_recovery_plan(self, plan):
            return [3]

        def _persist(self):
            return None

    harness = type("Harness", (), {"tx": Tx(), "workdir": tmp_path})()
    agent = type("Agent", (), {"harness": harness})()
    injected = {
        "faulty_path": "result.txt",
        "root_step": 3,
        "test_run_step": 9,
    }

    targets, localization = _auto_localize_policy(agent, injected, "causal")

    assert observed["command"][1] == "-c"
    assert "result.txt" not in observed["command"][-1]
    assert "TB_FAULT_MARKER" not in observed["command"][-1]
    assert observed["kwargs"]["trace_reads"] is False
    assert "read_paths" not in observed["kwargs"]
    assert targets == [3]
    assert localization["localization_matches_injected_root"] is True
    assert localization["localization_total_wall_s"] >= 0
    assert localization["localization_search_wall_s"] >= 0


def test_only_causal_uses_auto_localization_and_coarse_policies_are_blind(tmp_path):
    class Step:
        returncode = 1
        step_id = 7
        parents = []

    class Plan:
        verified = True
        root_step_ids = (3,)
        validation = "verified"
        confidence = 1.0

    class Tx:
        history = [
            type(
                "Record",
                (),
                {"step_id": 9, "argv": ["verify"], "parents": [3]},
            )()
        ]

        def __init__(self):
            self.applied = []
            steps = [
                type(
                    "LedgerStep",
                    (),
                    {"step_id": index, "parents": set(), "status": "active"},
                )()
                for index in range(8)
            ]
            self.ledger = type(
                "Ledger",
                (),
                {
                    "steps": steps,
                    "active_uncommitted": lambda ledger: ledger.steps,
                },
            )()

        def run_verifier(self, command, **kwargs):
            return Step()

        def plan_recovery(self, failed, **kwargs):
            return Plan()

        def apply_recovery_plan(self, plan):
            self.applied.append(("causal", tuple(plan.root_step_ids)))
            return [3]

        def rollback(self, root):
            self.applied.append(("rollback", root))
            return list(range(root, 10))

        def _persist(self):
            return None

    injected = {"root_step": 3, "test_run_step": 9}
    tx = Tx()
    harness = type("Harness", (), {"tx": tx, "workdir": tmp_path})()
    agent = type("Agent", (), {"harness": harness})()
    _, causal = _auto_localize_policy(agent, injected, "causal")
    assert tx.applied == [("causal", (3,))]
    assert causal["fault_location_source"] == "agenttx_localizer"
    assert causal["fault_location_disclosed"] is True

    expected = {
        "temporal_checkpoint": ("rollback", 3),
        "whole_branch_abort": ("rollback", 0),
        "crash_no_rollback": None,
    }
    for mode, application in expected.items():
        tx = Tx()
        harness = type("Harness", (), {"tx": tx, "workdir": tmp_path})()
        agent = type("Agent", (), {"harness": harness})()

        _, metadata = _apply_blind_policy(agent, injected, mode)

        assert tx.applied == ([] if application is None else [application])
        assert metadata["localized_root_steps"] == []
        assert metadata["fault_location_source"] == "hidden"
        assert metadata["fault_location_disclosed"] is False


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


def test_resume_keeps_completed_unsolved_attempts_and_retries_infrastructure_failures():
    completed = _row(
        success=False,
        tests_ok=False,
        finished=True,
        finish_called=True,
        committed=True,
        environment_error=False,
        error="",
    )
    assert _completed_attempt(completed) is True
    assert _completed_attempt({**completed, "environment_error": True}) is False
    assert _completed_attempt({**completed, "finished": False}) is False
    assert _completed_attempt({
        **completed,
        "error": "PermissionError: commit blocked by policy: host write",
    }) is True
    assert _completed_attempt({
        **completed,
        "error": "RecoveryProtectionError: recovery manifest was modified",
    }) is True
    assert _completed_attempt({
        **completed,
        "error": "CalledProcessError: docker pull failed",
    }) is False
    assert _completed_attempt({**completed, "error": "worker crashed", "finished": False}) is False


def test_parallel_sweep_can_report_failed_results_without_worker_failure():
    rows = [{"success": True}, {"success": False}]

    assert _result_exit_code(rows, oracle=False, allow_failures=False) == 1
    assert _result_exit_code(rows, oracle=False, allow_failures=True) == 0


def test_verifier_environment_error_only_marks_execution_failures():
    assert _verifier_environment_error({"tests_rc": "error"}) is True
    assert _verifier_environment_error({"tests_rc": "timeout"}) is True
    assert _verifier_environment_error({"tests_rc": 1}) is False
    assert _verifier_environment_error({"tests_rc": 4}) is False


def test_load_prior_rows_supports_large_harness_fields(tmp_path):
    import csv

    path = tmp_path / "official_tasks_raw.csv"
    row = {
        "suite": "swe",
        "task": "django__django-1",
        "mode": "causal",
        "repeat": "0",
        "harness_backend": "codex",
        "finished": "True",
        "finish_called": "True",
        "environment_error": "False",
        "success": "False",
        "harness_stdout": "x" * 140_000,
    }
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)

    loaded = _load_prior_rows(path)
    assert len(loaded) == 1
    assert loaded[0]["repeat"] == 0
    assert loaded[0]["finished"] is True
    assert loaded[0]["environment_error"] is False


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


def test_localization_wall_summary_keeps_legacy_rows_unmeasured():
    measured = _row(mode="causal", repeat=0)
    measured["localization_total_wall_s"] = 12.5
    legacy = _row(mode="causal", repeat=1)
    tokens = token_summaries([measured, legacy])[0]

    assert tokens["localization_wall_s_samples"] == 1
    assert tokens["localization_wall_s_p50"] == 12.5
    assert token_summaries([legacy])[0]["localization_wall_s_p50"] is None


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
    assert groups["causal"]["success_combined_tokens_mean"] == 100
    assert groups["temporal_checkpoint"]["success_combined_tokens_mean"] == 240
    assert groups["temporal_checkpoint"]["avoided_combined_tokens_mean"] == 140
    assert groups["whole_branch_abort"]["success_combined_tokens_mean"] == 340
    assert groups["whole_branch_abort"]["avoided_combined_tokens_mean"] == 240


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
    assert groups["temporal_checkpoint"]["avoided_combined_tokens_mean"] == -1_052_000
    assert groups["temporal_checkpoint"]["causal_minus_mode_tokens_mean"] == 1_052_000


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


@pytest.mark.parametrize('source',['.agenttx_faults/case.txt','./.agenttx_faults/case.txt','.hidden.py'])
def test_recovery_manifest_preserves_hidden_source_path_and_observed_state(tmp_path,source):
    docs=[DocSpec('recovery_notes/design.md','design',1)]
    ledger=Ledger()
    note=ledger.add_step('note',[Effect(str(tmp_path/docs[0].path),EffectKind.WRITE)])
    manifest=build_recovery_manifest(policy='causal',ledger=ledger,
        injected=dict(faulty_path=source,independent_steps=[note.step_id],derived_paths=['./.derived/output']),
        docs=docs,document_contents={docs[0].path:document_content('design',1,'case')},
        workdir=tmp_path,rollback_targets=[],path_exists={source:True,'./.derived/output':False})
    assert manifest['authoritative']
    assert manifest['invalidated'][0]['path']==Path(source).as_posix()
    assert manifest['invalidated'][0]['current_state']=='restored'
    assert manifest['invalidated'][1]['path']=='.derived/output'


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
    assert manifest["retained"] == []
    assert manifest["recreate_required"] == []
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
