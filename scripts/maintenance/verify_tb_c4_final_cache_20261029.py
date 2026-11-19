"""Check actual native model/final mounted cache guards without model/network calls."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from experiments.scripts.tb_verifier_c4_asset import ASSET, SHA256, CONTRACT


def main():
    audit = Path('/mnt/data/agenttx-runtime/validation/tb-c4-final-cache-20261029')
    pinned = audit.parent/'tb-c4-verified-cache-20261028/pinned-c4-heldout.gz'
    assert hashlib.file_digest(pinned.open('rb'), 'sha256').hexdigest() == SHA256
    folder = audit/('guard-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir()
    bin_dir = folder/'bin'
    bin_dir.mkdir()
    stub = bin_dir/'curl'
    stub.write_text('#!/bin/sh\nprintf called >> "$GUARD_CALLS"\n'
                    'while [ "$#" -gt 0 ]; do\n'
                    ' if [ "$1" = --output ]; then shift; output="$1"; fi\n shift\ndone\n'
                    '[ "${GUARD_HTTP416:-0}" != 1 ] || exit 22\n'
                    'cp --reflink=auto "$GUARD_PINNED" "$output"\n')
    stub.chmod(0o755)
    rows = []
    sources = []
    for harness in ('codex', 'sweagent'):
        cell = audit/('preflight-'+harness)/'clean_recovery'
        for entry, name in [('model', 'verified-c4-cache-run.sh'),
                            ('final', 'verified-c4-final-setup.sh')]:
            path = cell/name
            fixed = path.read_text()
            start = fixed.index('if ! ( echo "'+SHA256)
            end = fixed.index('fi\n', start)+3
            guard = fixed[start:end]
            sources.append(dict(harness=harness, entry=entry, path=str(path),
                                sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
            for case in ('complete', 'missing', 'corrupt'):
                asset = folder/f'{harness}-{entry}-{case}.gz'
                calls = asset.with_suffix('.calls')
                if case == 'complete':
                    subprocess.run(['cp', '--reflink=auto', str(pinned), str(asset)], check=True)
                elif case == 'corrupt':
                    asset.write_bytes(b'corrupt-cache')
                script = 'set -eu\n'+guard.replace(ASSET, str(asset))
                script += f'echo "{SHA256}  {asset}" | sha256sum -c -\n'
                env = dict(os.environ, PATH=str(bin_dir)+':/usr/bin:/bin',
                           GUARD_CALLS=str(calls), GUARD_PINNED=str(pinned),
                           GUARD_HTTP416='1' if case == 'complete' else '0')
                result = subprocess.run(['bash', '-c', script], env=env, capture_output=True,
                                        text=True, timeout=30)
                count = calls.read_text().count('called') if calls.exists() else 0
                assert result.returncode == 0 and count == (0 if case == 'complete' else 1), (
                    harness, entry, case, result.returncode, result.stderr, count)
                rows.append(dict(harness=harness, entry=entry, case=case,
                                 returncode=result.returncode, downloader_calls=count,
                                 checksum_passed=True))
                asset.unlink()
    # Reproduce the old final-entry mistake: even a complete shard invokes curl.
    original = Path('/mnt/data/agenttx-runtime/results/tb-dual-native-full-20261029-r58-session-visibility/retry-cells/reshard-c4-data/sweagent/clean_recovery/attempt-002/verifier-setup.sh').read_text()
    curl = next(line for line in original.splitlines() if line.startswith('curl ') and ASSET in line)
    calls = folder/'original.calls'
    env = dict(os.environ, PATH=str(bin_dir)+':/usr/bin:/bin', GUARD_CALLS=str(calls),
               GUARD_PINNED=str(pinned), GUARD_HTTP416='1')
    old = subprocess.run(['bash', '-c', curl], env=env, capture_output=True, timeout=20)
    assert old.returncode == 22 and calls.read_text() == 'called'
    proof = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), returncode=0,
                 contract=CONTRACT, no_model_calls=True, no_network_calls=True,
                 active_workers_modified=False, host_forwarding_modified=False,
                 original_final_entry_http416_reproduced=True, cases=rows,
                 mounted_sources=sources, assertions_changed=False, dataset_bytes_changed=False)
    (audit/'regression-result.json').write_text(json.dumps(proof, indent=2)+'\n')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
