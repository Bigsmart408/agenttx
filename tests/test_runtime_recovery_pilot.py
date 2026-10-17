"""Verify the diagnostic workload's corruption and independent byte contracts."""
import subprocess
import sys

from experiments.scripts.bench_runtime_recovery_pilot import expected_artifacts, render_workload, workload_spec
from experiments.workloads.intra_turn_recovery_dag import build_intra_turn_plan


def test_corruption_propagates_through_real_parent_reads_and_repair_restores(tmp_path):
    plan = build_intra_turn_plan(16, fault_position=.25, closure_ratio=.25,
                                shape="layered", interleaving="alternating", seed=0)
    spec = workload_spec(plan)
    expected = expected_artifacts(spec)
    (tmp_path / "workload.py").write_text(render_workload(spec))
    def call(*args):
        return subprocess.run([sys.executable, "workload.py", *map(str, args)], cwd=tmp_path,
                              capture_output=True, text=True)
    for step in plan.steps:
        argv = ["run", step.index]
        if step.index in plan.fault_roots:
            argv.append("prior-version-value")
        assert call(*argv).returncode == 0
    invalid = {i for i, s in enumerate(spec) if (tmp_path / s["path"]).read_text() != expected[i]}
    assert invalid == set(plan.oracle_closure)
    assert call("check").returncode == 1
    for i in sorted(invalid):
        (tmp_path / spec[i]["path"]).unlink()
    assert call("check-present").returncode == 0
    assert call("check").returncode == 1
    retained = {i: (tmp_path / s["path"]).read_bytes() for i, s in enumerate(spec) if i not in invalid}
    for i in sorted(invalid):
        assert call("run", i).returncode == 0
    assert call("check").returncode == 0
    assert all((tmp_path / s["path"]).read_text() == expected[i] for i, s in enumerate(spec))
    assert all((tmp_path / spec[i]["path"]).read_bytes() == data for i, data in retained.items())
