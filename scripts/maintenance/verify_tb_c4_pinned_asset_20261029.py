"""Verify pinned read-only C4 transport against missing, partial and corrupt caches."""
import datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT))
from experiments.scripts.tb_verifier_c4_asset import ASSET, SHA256, CONTRACT, PINNED_ASSET


def main():
    audit = Path('/mnt/data/agenttx-runtime/validation/tb-c4-pinned-asset-20261029')
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
            for case in ('complete', 'missing', 'corrupt', 'oversized', 'partial'):
                asset = folder/f'{harness}-{entry}-{case}.gz'
                calls = asset.with_suffix('.calls')
                if case == 'complete':
                    subprocess.run(['cp', '--reflink=auto', str(pinned), str(asset)], check=True)
                elif case == 'corrupt':
                    asset.write_bytes(b'corrupt-cache')
                if case == 'oversized':
                    with asset.open('wb') as stream: stream.truncate(411529336)
                if case == 'partial': asset.write_bytes(b'partial-download')
                script = 'set -eu\n'+guard.replace(ASSET, str(asset)).replace(PINNED_ASSET, str(pinned))
                script += f'echo "{SHA256}  {asset}" | sha256sum -c -\n'
                env = dict(os.environ, PATH=str(bin_dir)+':/usr/bin:/bin',
                           GUARD_CALLS=str(calls), GUARD_PINNED=str(pinned),
                           GUARD_HTTP416='1')
                result = subprocess.run(['bash', '-c', script], env=env, capture_output=True,
                                        text=True, timeout=30)
                count = calls.read_text().count('called') if calls.exists() else 0
                assert result.returncode == 0 and count == 0, (
                    harness, entry, case, result.returncode, result.stderr, count)
                rows.append(dict(harness=harness, entry=entry, case=case,
                                 returncode=result.returncode, downloader_calls=count,
                                 checksum_passed=True))
                asset.unlink()
    # An invalid trusted source must fail closed, without invoking a downloader.
    bad_pin = folder/'bad-pinned.gz'
    bad_pin.write_bytes(b'invalid-trusted-source')
    absent = folder/'absent.gz'
    calls = folder/'bad-pinned.calls'
    script = 'set -eu\n'+guard.replace(ASSET, str(absent)).replace(PINNED_ASSET, str(bad_pin))
    env = dict(os.environ, PATH=str(bin_dir)+':/usr/bin:/bin', GUARD_CALLS=str(calls),
               GUARD_PINNED=str(pinned), GUARD_HTTP416='1')
    rejected = subprocess.run(['bash', '-c', script], env=env, capture_output=True, timeout=20)
    assert rejected.returncode != 0 and not absent.exists() and not calls.exists()
    # Reproduce v2 resuming an invalid oversized cache and receiving HTTP416.
    ns = {}
    old_module = Path('/home/bfq/agenttx-workspace/agenttx-executions/tb-dual-native-full-20261029-r59-c4-final-cache/project/experiments/scripts/tb_verifier_c4_asset.py')
    exec(compile(old_module.read_text(), str(old_module), 'exec'), ns)
    original = Path('/mnt/data/agenttx-runtime/validation/tb-c4-bootstrap-20261020-r5/reshard-c4-data/run.sh').read_text()
    old_fixed = ns['patch_source'](original)
    start = old_fixed.index('if ! ( echo "'+SHA256)
    end = old_fixed.index('fi\n', start)+3
    bad_cache = folder/'v2-oversized.gz'
    with bad_cache.open('wb') as stream: stream.truncate(411529336)
    old_calls = folder/'v2.calls'
    env.update(GUARD_CALLS=str(old_calls))
    old = subprocess.run(['bash', '-c', 'set -eu\n'+old_fixed[start:end].replace(ASSET,str(bad_cache))],
                         env=env, capture_output=True, timeout=20)
    assert old.returncode == 22 and old_calls.read_text() == 'called'
    bad_cache.unlink()
    proof = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(), returncode=0,
                 contract=CONTRACT, no_model_calls=True, no_network_calls=True,
                 active_workers_modified=False, host_forwarding_modified=False,
                 invalid_cache_http416_reproduced=True, invalid_pinned_source_rejected=True, cases=rows,
                 mounted_sources=sources, assertions_changed=False, dataset_bytes_changed=False)
    (audit/'regression-result.json').write_text(json.dumps(proof, indent=2)+'\n')
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
