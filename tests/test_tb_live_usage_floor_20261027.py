from pathlib import Path

from scripts.maintenance.verify_tb_live_workers_20261026 import previous_floor


def test_floor_keeps_missing_main_usage_without_summing_overlapping_snapshots():
    folder=Path('/results/retry-cells/task/codex/checkpoint/attempt-001/checkpoint')
    snapshots=[('first',{'workers':[dict(result_path=str(folder/'host-result.json'),recorded_tokens=900000,actual_model_data=True)]}),
               ('overlap',{'workers':[dict(result_path=str(folder/'host-result.json'),recorded_tokens=1100000,actual_model_data=True)]}),
               ('replay-only',{'workers':[dict(result_path=str(folder/'host-result.json'),recorded_tokens=25000,actual_model_data=True)]})]
    assert previous_floor(folder,snapshots)==(1100000,'overlap')


def test_legacy_path_evidence_never_crosses_attempt_or_policy_boundaries():
    folder=Path('/results/attempt-001/checkpoint')
    rows=[dict(paths=['/results/attempt-002/checkpoint/session/rollout.jsonl'],recorded_tokens=9000000,actual_model_data=True),
          dict(paths=['/results/attempt-001/checkpoint-other/session/rollout.jsonl'],recorded_tokens=8000000,actual_model_data=True),
          dict(paths=[str(folder/'session/rollout.jsonl')],recorded_tokens=300000,actual_model_data=True)]
    assert previous_floor(folder,[('legacy',{'workers':rows})])==(300000,'legacy')


def test_absent_or_unverified_usage_remains_unknown():
    folder=Path('/results/attempt-001/checkpoint')
    rows=[dict(result_path=str(folder/'host-result.json'),recorded_tokens=None,actual_model_data=True),
          dict(result_path=str(folder/'host-result.json'),recorded_tokens=700000,actual_model_data=False)]
    assert previous_floor(folder,[('missing',{'workers':rows})])==(0,None)
