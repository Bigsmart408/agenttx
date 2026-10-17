"""Keep private model credentials out of retained experiment evidence."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path


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
