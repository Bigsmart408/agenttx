"""Keep private model credentials out of retained experiment evidence."""
from __future__ import annotations
import hashlib
import json
import os
import pwd
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def task_user_codex_profile(task_user, *, parent=None):
    """Expose unchanged task-private profile copies to the original task uid.

    Host profile mounts can be mode 0700/0600 for another uid. Never fall back
    to Codex defaults merely because the original task account cannot read
    them, and never relax the host profile's permissions.
    """
    account = pwd.getpwnam(task_user)
    original = {key: os.environ.get(key) for key in ('CODEX_CONFIG_FILE', 'CODEX_AUTH_FILE')}
    sources = {key: Path(value) for key, value in original.items() if value}
    if set(sources) != set(original) or not all(p.is_file() for p in sources.values()):
        raise RuntimeError('original task user requires the existing explicit Codex profile')
    directory = Path(tempfile.mkdtemp(prefix='agenttx-task-profile-', dir=parent or '/run'))
    try:
        proof = {}
        for key, source in sources.items():
            destination = directory / source.name
            body = source.read_bytes()
            destination.write_bytes(body)
            os.chown(destination, account.pw_uid, account.pw_gid)
            destination.chmod(0o600)
            assert destination.read_bytes() == body
            os.environ[key] = str(destination)
            proof[key] = dict(bytes_identical=True, mode='0600',
                uid=account.pw_uid, gid=account.pw_gid,
                sha256=hashlib.sha256(body).hexdigest())
        os.chown(directory, account.pw_uid, account.pw_gid)
        directory.chmod(0o700)
        yield dict(files=proof, host_profile_modified=False, provider_configuration_changed=False)
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(directory)


def private_values():
    values = {os.environ.get('OPENAI_API_KEY', ''), os.environ.get('CODEX_API_KEY', '')}
    auth = os.environ.get('CODEX_AUTH_FILE')
    if auth and Path(auth).is_file():
        data = json.loads(Path(auth).read_text())
        values.add(data.get('OPENAI_API_KEY', ''))
    return {value for value in values if value}


def redact_credentials(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, '[REDACTED:MODEL_CREDENTIAL]')
        return value
    if isinstance(value, list):
        return [redact_credentials(item, secrets) for item in value]
    if isinstance(value, dict):
        return {key: redact_credentials(item, secrets) for key, item in value.items()}
    return value


def purge_private_codex_auth(output, secrets):
    """Remove only generated Codex auth scratch and its exact blob copies.

    Unlinking preserves any original private configuration inode. Task files and
    arbitrary matching content are never removed by this cleanup.
    """
    output = Path(output)
    encoded = [secret.encode() for secret in secrets]
    auth_files = [p for p in output.rglob('auth.json') if not p.is_symlink()
                  and any(part.startswith('.agenttx-codex-') for part in p.parts)]
    hashes = set(); sizes = set(); removed = []
    for path in auth_files:
        body = path.read_bytes()
        if not any(secret in body for secret in encoded):
            continue
        digest = hashlib.sha256(body).hexdigest(); hashes.add(digest); sizes.add(len(body))
        removed.append(dict(path=str(path.relative_to(output)), sha256=digest))
        path.unlink()
    # Layer addresses may include mode/owner metadata, so compare the actual
    # bytes rather than assuming the filename equals sha256(file contents).
    for blob in output.glob('session/layers/blobs/*/*'):
        if blob.is_file() and not blob.is_symlink() and blob.stat().st_size in sizes:
            digest = hashlib.sha256(blob.read_bytes()).hexdigest()
            if digest in hashes:
                removed.append(dict(path=str(blob.relative_to(output)), sha256=digest))
                blob.unlink()
    return removed
