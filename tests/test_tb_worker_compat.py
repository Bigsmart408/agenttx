import hashlib
import json
from pathlib import Path
import pytest
from experiments.scripts import tb_worker_compat as compat


def test_worker_extension_is_scoped_to_original_image_and_rejects_tampering(tmp_path,monkeypatch):
    cache=tmp_path/'cache';cache.mkdir()
    native=cache/'_tiktoken.so';native.write_bytes(b'verified-native')
    proof=dict(native_filename=native.name,version='0.14.0',sweagent_import_passed=True,
        token_parity_cases=1008,model_calls=0)
    p=cache/'proof.json';p.write_text(json.dumps(proof))
    monkeypatch.setattr(compat,'CACHE',cache)
    monkeypatch.setattr(compat,'PROOF_SHA256',hashlib.sha256(p.read_bytes()).hexdigest())
    monkeypatch.setattr(compat,'NATIVE_SHA256',hashlib.sha256(native.read_bytes()).hexdigest())
    python=tmp_path/'venv/bin/python'
    target=python.parent.parent/'lib/python3.11/site-packages/tiktoken'/native.name
    target.parent.mkdir(parents=True);target.touch()
    assert compat.worker_compatibility('other-task','other-image',python) is None
    assert compat.worker_compatibility(compat.TASK,compat.IMAGE,python)[:2]==(native,target)
    with pytest.raises(AssertionError):compat.worker_compatibility(compat.TASK,'other-image',python)
    native.write_bytes(b'tampered')
    with pytest.raises(AssertionError):compat.worker_compatibility(compat.TASK,compat.IMAGE,python)
