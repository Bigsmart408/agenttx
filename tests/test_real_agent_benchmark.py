from experiments.scripts.bench_real_agent import summarize


def test_real_agent_summary_reports_tail_and_success_rates() -> None:
    result = summarize(
        [
            {
                "model": "test-model",
                "wall_s": 1.0,
                "finished": True,
                "tool_calls": 5,
                "host_polluted_before_commit": False,
                "tests_rc": 0,
                "success": True,
            },
            {
                "model": "test-model",
                "wall_s": 3.0,
                "finished": False,
                "tool_calls": 7,
                "host_polluted_before_commit": False,
                "tests_rc": 1,
                "success": False,
            },
        ]
    )
    assert result["wall_p50_s"] == 2.0
    assert result["wall_p95_s"] == 2.9
    assert result["finished_rate"] == 0.5
    assert result["success_rate"] == 0.5
    assert result["host_leak_rate"] == 0.0
