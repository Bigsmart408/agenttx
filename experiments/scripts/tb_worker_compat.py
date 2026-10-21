"""Hash-pinned worker extension for the original Ubuntu 18.04 task image."""
import hashlib
import json
from pathlib import Path

CACHE=Path('/mnt/data/agenttx-runtime/cache/tb-worker-compat/tiktoken-0.14.0-glibc227')
TASK='accelerate-maximal-square'
IMAGE='sha256:30c868a837e6b756aab5ce3cb2d9a26830f77cb96f566c307e10d71b61481d47'
PROOF_SHA256='6b3faf71d067249b79043bbe3d4ec011bb0780caf92a18762d17690b28e5d6ef'
NATIVE_SHA256='7c99833613831e30d9b1fed4685ead8ea23f95b8fc332782b8c23335d391cd0a'


def worker_compatibility(task,image,python):
    if task!=TASK:return None
    assert image==IMAGE,'worker compatibility is restricted to its verified task image'
    proof_path=CACHE/'proof.json'
    assert hashlib.sha256(proof_path.read_bytes()).hexdigest()==PROOF_SHA256
    proof=json.loads(proof_path.read_text())
    native=CACHE/proof['native_filename']
    assert native.parent==CACHE and hashlib.sha256(native.read_bytes()).hexdigest()==NATIVE_SHA256
    assert proof['version']=='0.14.0' and proof['sweagent_import_passed']
    assert proof['token_parity_cases']==1008 and proof['model_calls']==0
    targets=list((python.parent.parent/'lib').glob('python3.11/site-packages/tiktoken/'+native.name))
    assert len(targets)==1,'expected Python 3.11 worker tiktoken installation'
    return native,targets[0],proof
