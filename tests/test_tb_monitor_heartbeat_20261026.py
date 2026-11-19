import datetime
import json
import os
import pytest

from scripts.maintenance.monitor_tb_live_progress_20261026 import coordinator_status


def test_dead_coordinator_and_stale_progress_trigger_health_failure(tmp_path):
    now=datetime.datetime.fromisoformat('2026-10-26T14:00:00+00:00').timestamp()
    progress=dict(coordinator_pid=123,updated_at='2026-10-26T14:00:00+00:00')
    assert not coordinator_status(progress,now,tmp_path)['coordinator_healthy']
    path=tmp_path/'123';path.mkdir();(path/'stat').write_text('123 (python) S 1 2 3')
    assert coordinator_status(progress,now,tmp_path)['coordinator_healthy']
    assert not coordinator_status(progress,now+91,tmp_path)['coordinator_healthy']
    (path/'stat').write_text('123 (python) T 1 2 3')
    assert not coordinator_status(progress,now,tmp_path)['coordinator_healthy']


@pytest.mark.parametrize('stop,expected', [(None, False), ('new_environment_exception_requires_review', True)])
def test_dispatch_halt_alerts_immediately_even_with_fresh_healthy_coordinator(tmp_path, monkeypatch, stop, expected):
    import scripts.maintenance.monitor_tb_live_progress_20261026 as monitor
    root=tmp_path/'results/cohort';root.mkdir(parents=True)
    pointer=tmp_path/'validation';pointer.mkdir()
    (pointer/'active-tb-dual-native.json').write_text(json.dumps(dict(root=str(root))))
    now=datetime.datetime.now(datetime.timezone.utc).isoformat()
    progress=dict(coordinator_pid=os.getpid(), updated_at=now,
                  tasks=[dict(task='waiting-task', harness='codex', policy='causal', state='pending')],
                  retry_tasks=[], counts=dict(pending=1), retry_counts={}, active=[],
                  stop_reason=stop, provider_gate=False)
    (root/'progress.json').write_text(json.dumps(progress))
    monkeypatch.setattr(monitor, 'D', tmp_path)
    result=monitor.sample()
    assert result['coordinator_healthy']
    assert result['seconds_without_real_terminal']==0
    assert result['investigation_due'] is expected
