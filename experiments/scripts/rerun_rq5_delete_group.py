#!/usr/bin/env python3
"""Repeat the complete deletion block after all history verification has ended."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def main():
    root = Path(__file__).parent
    source = root/'results'
    output = root/'deletion-retest'
    assert json.loads((source/'progress.json').read_text())['state'] == 'complete'
    verification = json.loads((root/'all-generation-verification.json').read_text())
    assert all(r['status'] == 'passed' for r in verification['results'])
    assert not output.exists()
    output.mkdir()
    manifest = json.loads((source/'manifest.json').read_text())
    plan = [r for r in manifest['plan'] if r['case'].get('operation') == 'unlink'
            and r['phase'] in ('warmup', 'measured')]
    assert len(plan) == 77

    def save(name, value):
        path = output/name
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(value, indent=2)+'\n')
        temp.replace(path)

    save('manifest.json', dict(plan=plan,
        reason='History verifier briefly overlapped the end of the original deletion block; repeat every deletion variant and repetition.',
        original_manifest_sha256=hashlib.sha256((source/'manifest.json').read_bytes()).hexdigest(),
        original_run='results', runner='bench_rq5_snapshot_try_campaign.py',
        wrapper_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        model_calls=0))
    for completed, item in enumerate(plan):
        save('progress.json', dict(state='running', completed=completed, total=77, current=item))
        started = time.perf_counter_ns()
        os.sync()
        item['precondition_sync_ns'] = time.perf_counter_ns()-started
        r = subprocess.run([sys.executable, str(root/'bench_rq5_snapshot_try_campaign.py'),
                            '--output', str(output), '--one', json.dumps(item)])
        if r.returncode:
            save('progress.json', dict(state='failed', completed=completed, total=77, current=item))
            raise SystemExit(r.returncode)
    save('progress.json', dict(state='complete', completed=77, total=77))


if __name__ == '__main__':
    main()
