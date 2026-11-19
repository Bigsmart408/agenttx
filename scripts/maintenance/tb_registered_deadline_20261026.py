"""Recognize retained deadline exits without treating reconnect notices as outages."""
import hashlib
import json
from pathlib import Path
import re


def registered_deadline_evidence(row):
    path = Path(row['result_path'])
    if not path.exists():
        return None
    result = json.loads(path.read_text())
    if not (result.get('harness') == 'codex' and result.get('completed')
            and result.get('container_returncode') == 0
            and result.get('model_returncode') == 124
            and result.get('official_tests_rc') == 124
            and result.get('status') == 'verifier_timeout'
            and result.get('model_session_wall_s') == 3600
            and result.get('verifier_wall_s') == 1800):
        return None
    native = path.parent / 'model-result.json'
    retained = path.parent / 'accounting-summary.json'
    if native.exists():
        data = json.loads(native.read_text())
        stderr = data.get('stderr', '')
        events = data.get('events', [])
        evidence_path = native
    elif retained.exists():
        data = (json.loads(retained.read_text()).get('model_summaries') or {}).get('model-result.json') or {}
        stderr = data.get('stderr_excerpt', '')
        events = data.get('usage_and_terminal_events', [])
        evidence_path = retained
    else:
        return None
    if data.get('returncode') != 124 or not re.search(r'^external harness timeout after 3600\.0s$', stderr, re.M):
        return None
    if re.search(r'Connection refused|APIConnectionError|Connection error\.|error sending request for url|connection failed|HTTP[^\n]*\b5\d\d\b', stderr, re.I):
        return None
    notices = 0
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get('type') in ('turn.failed', 'response.failed'):
            return None
        if event.get('type') == 'error':
            match = re.fullmatch(r'Reconnecting\.\.\. (\d+)/(\d+) \(stream disconnected before completion: stream closed before response\.completed\)', str(event.get('message', '')))
            if not match or not (0 < int(match[1]) < int(match[2])):
                return None
            notices += 1
    verifier = path.parent / 'verifier.stderr.log'
    if not verifier.exists() or not re.search(r'^external harness timeout after 1800\.0s$', verifier.read_text(), re.M):
        return None
    return dict(classification='registered_model_and_verifier_deadlines',
                model_limit_s=3600, verifier_limit_s=1800, reconnect_notices=notices,
                provider_health_not_inferred=True, task_success=False,
                incomplete_cost_comparison_exclusion=True, automatic_retry=False,
                result_path=str(path), result_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                model_evidence_path=str(evidence_path),
                model_evidence_sha256=hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                verifier_evidence_sha256=hashlib.sha256(verifier.read_bytes()).hexdigest())
