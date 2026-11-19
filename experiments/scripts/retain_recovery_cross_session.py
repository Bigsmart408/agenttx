"""Bound storage of this cohort's disposable terminal TB sessions only."""
from __future__ import annotations
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(2**20), b''):
            h.update(block)
    return h.hexdigest()


def retain(cohort, cell_folder):
    cohort, cell_folder = Path(cohort).resolve(), Path(cell_folder).resolve()
    registration = json.loads((cohort/'registration.json').read_text())
    assert registration['protocol'] in ('agenttx.recovery-cross-ablation/v1', 'agenttx.recovery-cross-ablation/v2',
                                        'agenttx.recovery-cross-ablation/v3')
    assert cell_folder.is_relative_to(cohort/'cells'/'tb')
    variants=list((cell_folder/'native').glob('*/host-result.json'))
    assert len(variants)==1
    session=variants[0].parent/'session'
    result = session.parent/'host-result.json'
    assert result.is_file()
    marker = session.parent/'session-retention.json'
    if marker.exists() or not session.exists():
        return
    assert not session.is_symlink()
    result_sha = digest(result)
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        mountpoint = Path(line.split()[4].replace('\\040', ' '))
        assert not mountpoint.is_relative_to(session), 'terminal session still mounted'
    records = []
    # Keep original native Codex response/usage records at their original paths.
    # Also preserve their path in a detached copy before releasing overlay data.
    for source in (session/'upperdir').glob('**/.agenttx-codex-*/codex/sessions/**/*.jsonl'):
        if source.is_symlink() or not source.is_file():
            continue
        relative = source.relative_to(session)
        destination = session.parent/'native-transcripts'/relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        checksum = digest(source)
        assert digest(destination) == checksum
        records.append(dict(path=str(relative), retained_path=str(destination), sha256=checksum))
    # Lossless compression retains raw syscall evidence without repeating it
    # as uncompressed text for every completed attempt.
    traces = []
    for path in session.glob('*.log'):
        if not path.name.startswith('worker-strace-'):
            continue
        checksum = digest(path)
        target = path.with_suffix(path.suffix+'.gz')
        with path.open('rb') as src, gzip.open(target, 'wb', compresslevel=1) as dst:
            shutil.copyfileobj(src, dst, length=2**20)
        h = hashlib.sha256()
        with gzip.open(target, 'rb') as stream:
            for block in iter(lambda: stream.read(2**20), b''):
                h.update(block)
        assert h.hexdigest() == checksum
        traces.append(dict(path=path.name, sha256=checksum, bytes=path.stat().st_size,
                           compressed_bytes=target.stat().st_size))
        path.unlink()
    payloads = ('upperdir', 'workdir', 'temproot', 'layers')
    # Commit the retention scope before clearing disposable package/file bytes.
    proof = dict(schema='agenttx.recovery-cross-session-retention/v1',
        host_result_sha256=result_sha, model_logs_and_ledgers_preserved=True,
        native_transcripts=records, lossless_traces=traces,
        disposable_payloads=list(payloads), snapshots_retained=False)
    marker.with_suffix('.pending.json').write_text(json.dumps(proof, indent=2)+'\n')
    for name in payloads:
        path = session/name
        if path.is_symlink():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    # Recreate the exact native transcript paths used by accounting readers.
    for record in records:
        path = session/record['path']
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(record['retained_path'])
    assert digest(result) == result_sha
    marker.with_suffix('.pending.json').replace(marker)
    for path in (marker, *session.rglob('*'), *session.parent.joinpath('native-transcripts').rglob('*')):
        if os.geteuid()==0 and not path.is_symlink():
            os.chown(path, 1003, 1003)
