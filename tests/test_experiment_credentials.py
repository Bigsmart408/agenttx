import hashlib
import os
from experiments.scripts.experiment_credentials import redact_credentials, purge_private_codex_auth


def test_nested_runtime_evidence_redacts_keys_without_mutating_live_commands():
    original={'records':[{'argv':['bash','-c','export OPENAI_API_KEY=test-private-value\nprintf done']}], 'code':0}
    public=redact_credentials(original, {'test-private-value'})
    assert 'test-private-value' not in str(public)
    assert 'test-private-value' in str(original)
    assert public['code']==0


def test_timeout_auth_cleanup_unlinks_scratch_without_changing_private_source(tmp_path):
    source=tmp_path/'private-profile/auth.json';source.parent.mkdir();source.write_text('test-private-value')
    output=tmp_path/'cell'; auth=output/'session/layers/before_0018/home/task/.agenttx-codex-owned/codex/auth.json'
    auth.parent.mkdir(parents=True);os.link(source,auth)
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    blob=output/'session/layers/blobs/ab/metadata-addressed-blob';blob.parent.mkdir(parents=True);os.link(source,blob)
    task=output/'workspace/task.txt';task.parent.mkdir();task.write_text('test-private-value')
    removed=purge_private_codex_auth(output, {'test-private-value'})
    assert len(removed)==2
    assert not auth.exists() and not blob.exists()
    assert source.read_text()=='test-private-value'
    assert task.read_text()=='test-private-value'
