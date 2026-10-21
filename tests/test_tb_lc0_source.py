import hashlib
import pytest
from experiments.scripts.repair_tb_lc0_verifier_source import signed_commit_bytes


def test_signed_commit_is_reproduced_only_when_every_original_byte_matches():
    headers='tree '+'1'*40+'\nauthor A <a@example.com> 123 +0200\ncommitter C <c@example.com> 123 +0300'
    signature='-----BEGIN PGP SIGNATURE-----\n\npublic-signature\n-----END PGP SIGNATURE-----\n'
    message='original message'
    # GitHub payload can omit the final LF and the empty signature continuation.
    raw=(headers+'\ngpgsig '+signature.replace('\n','\n ')+'\n\n'+message+'\n').encode()
    sha=hashlib.sha1(b'commit '+str(len(raw)).encode()+b'\0'+raw).hexdigest()
    metadata=dict(sha=sha,verification=dict(verified=True,payload=headers+'\n\n'+message,signature=signature))
    assert signed_commit_bytes(metadata)==raw
    metadata['verification']['signature']=signature.replace('public-signature','changed-signature')
    with pytest.raises(RuntimeError,match='Cannot reproduce'):signed_commit_bytes(metadata)


def test_unverified_signature_metadata_is_rejected():
    with pytest.raises(AssertionError):signed_commit_bytes(dict(verification=dict(verified=False)))
