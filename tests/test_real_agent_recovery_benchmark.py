from experiments.scripts.bench_real_agent_recovery import summarize


def test_real_agent_recovery_summary_rates():
    rows = [
        {
            "model": "test-model",
            "wall_s": 10.0,
            "success": True,
            "selected_root": True,
            "causal_targets_correct": True,
            "independent_retained": True,
            "derived_removed": True,
            "tests_rc": 0,
            "host_polluted_before_commit": False,
        },
        {
            "model": "test-model",
            "wall_s": 20.0,
            "success": False,
            "selected_root": False,
            "causal_targets_correct": False,
            "independent_retained": True,
            "derived_removed": False,
            "tests_rc": 1,
            "host_polluted_before_commit": False,
        },
    ]
    result = summarize(rows)
    assert result["wall_p50_s"] == 15.0
    assert result["success_rate"] == 0.5
    assert result["root_selection_rate"] == 0.5
    assert result["independent_retention_rate"] == 1.0
    assert result["host_leak_rate"] == 0.0
