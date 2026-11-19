"""Recognize a fully accounted Codex turn after recoverable reconnect notices."""
import hashlib
import json
import re
from pathlib import Path


NOTICE = 'Reconnecting... waiting for network (Connection failed: error sending request)'


def recovered_connection_evidence(row):
    """Return proof only for a successful model terminal, never task success."""
    if row.get('state') != 'completed' or row.get('harness') != 'codex':
        return None
    path = Path(row.get('result_path', ''))
    accounting_path = path.parent / 'accounting-summary.json'
    try:
        raw = path.read_bytes()
        result = json.loads(raw)
        accounting_raw = accounting_path.read_bytes()
        accounting = json.loads(accounting_raw)
    except (OSError, ValueError):
        return None
    if not isinstance(result, dict) or not isinstance(accounting, dict):
        return None
    result_sha256 = hashlib.sha256(raw).hexdigest()
    if accounting.get('host_result_sha256') != result_sha256:
        return None
    usage = result.get('api_usage')
    if not (result.get('completed') is True
            and result.get('model_finished') is True
            and result.get('model_returncode') == 0
            and result.get('container_returncode') == 0
            and isinstance(usage, dict) and usage.get('complete') is True):
        return None
    summaries = accounting.get('model_summaries', {})
    if not isinstance(summaries, dict):
        return None
    model = summaries.get('model-result.json')
    replay = summaries.get('replay-result.json')
    if not isinstance(model, dict) or model.get('returncode') != 0:
        return None
    if replay is not None and (not isinstance(replay, dict) or replay.get('returncode') != 0):
        return None
    events = model.get('usage_and_terminal_events')
    if not isinstance(events, list) or not events or not all(isinstance(e, dict) for e in events):
        return None
    if events[-1].get('type') != 'turn.completed':
        return None
    notices = 0
    for event in events:
        kind = event.get('type')
        if kind == 'turn.failed':
            return None
        if kind == 'error':
            message = event.get('message', '')
            stream_notice = isinstance(message, str) and re.fullmatch(
                r'Reconnecting\.\.\. [1-5]/5 \(stream disconnected before completion: stream closed before response\.completed\)',
                message)
            if message != NOTICE and not stream_notice:
                return None
            notices += 1
    if not notices:
        return None
    return dict(
        classification='recovered_model_reconnect_followed_by_completed_turn',
        result_path=str(path), result_sha256=result_sha256,
        accounting_path=str(accounting_path),
        accounting_sha256=hashlib.sha256(accounting_raw).hexdigest(),
        retained_model_result_sha256=model.get('original_sha256'),
        recovered_notices=notices, terminal_event=events[-1],
        model_returncode=0, usage_complete=True,
        official_success=result.get('official_success'),
        task_outcome_unchanged=True, additional_retry_authorized=False,
        no_model_calls=True)
