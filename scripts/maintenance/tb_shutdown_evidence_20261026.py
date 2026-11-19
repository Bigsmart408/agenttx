"""Match immutable evidence of an enforced, already-stalled worker shutdown."""
import hashlib
import json
from pathlib import Path

D = Path('/mnt/data/agenttx-runtime/validation')


def shutdown_evidence(row, validation_root=D):
    candidates = [validation_root / 'tb-worker-shutdown-repair-20261026/shutdown-enforcement.json']
    candidates += sorted((validation_root / 'tb-live-monitor-20261026/shutdown-releases').glob('*.json'))
    for path in candidates:
        if not path.exists():
            continue
        proof = json.loads(path.read_text())
        if proof.get('affected_result_path') != row.get('result_path'):
            continue
        if path.parent.name == 'shutdown-releases' and proof.get('enforcement_returncode') != 0:
            continue
        return dict(proof, evidence_path=str(path), evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    return None
