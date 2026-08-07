from motivation.summarize_optimization_history import summarize_history


def test_motivation_history_marks_snapshot_stage_metric() -> None:
    rows = summarize_history(
        [
            {
                "iteration": 5,
                "snapshot": "worker",
                "optimization": "persistent worker",
                "before_full_ms_per_step": 100.0,
                "after_full_ms_per_step": 50.0,
                "correct": True,
                "note": "endpoint measurement",
            },
            {
                "iteration": 6,
                "snapshot": "snapshot",
                "optimization": "incremental snapshot",
                "correct": True,
                "note": "snapshot stage 0.384 -> 0.158 s over 63 incremental calls",
            },
        ]
    )
    assert rows[0]["improvement_pct"] == 50.0
    assert rows[1]["metric"] == "snapshot_stage_s"
    assert rows[1]["before"] == 0.384
    assert rows[1]["after"] == 0.158
