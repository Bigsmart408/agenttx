#!/usr/bin/env python3
"""Register 6,000 Codex cells against the frozen SWE-agent input/runtime."""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
os.environ.setdefault('AGENTTX_DOCKER_HOST', 'unix:///run/agenttx-home-docker/docker.sock')
from experiments.scripts import run_system_microbenchmark as base

POLICIES = ['causal', 'crash_no_rollback', 'turn_checkpoint',
            'temporal_checkpoint', 'whole_branch_abort']
REFERENCE = Path('/mnt/data/agenttx-runtime/results/system-micro-canonical400-paired-20261030-r2')
REFERENCE_SOURCE = ROOT.parent / 'agenttx-executions' / REFERENCE.name / 'project'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', required=True, type=Path)
    parser.add_argument('--workers', type=int, default=8)
    args = parser.parse_args()
    audit = ROOT / 'review/codex-controlled-6000-20261107'
    ref = audit / 'relocated-reference'
    ref.mkdir(parents=True, exist_ok=True)
    registration = json.loads((REFERENCE / 'registration.json').read_text())
    registration['frozen_source'] = str(REFERENCE_SOURCE)
    base.save(ref / 'registration.json', registration)
    for name in ('source-manifest.json', 'cells.json'):
        shutil.copy2(REFERENCE / name, ref / name)
    args.policies = POLICIES
    args.reference_cohort = ref
    args.include_context_ablation = False
    base.prepare(args)
    reg = json.loads((args.cohort / 'registration.json').read_text())
    source = Path(reg['frozen_source'])
    additions = ['codex_micro_usage.py', 'bench_codex_controlled_20261107.py',
                 'prepare_codex_controlled_20261107.py']
    for name in additions:
        shutil.copy2(ROOT / 'experiments/scripts' / name, source / 'experiments/scripts' / name)
    entry = source / 'experiments/scripts/run_system_microbenchmark.py'
    text = entry.read_text()
    old = 'else "bench_runtime_recovery_system.py"'
    assert text.count(old) == 1
    text = text.replace(old, 'else "bench_codex_controlled_20261107.py"')
    old = '"--harness", "sweagent"'
    assert text.count(old) == 1
    text = text.replace(old, '"--harness", "codex"')
    old = 'if phase == "runtime_gate" and cell["state"] != "passed":'
    assert text.count(old) == 1
    text = text.replace(old, 'if phase in ("runtime_gate", "model_canary") and cell["state"] != "passed":')
    old = 'TRY_BIND_READONLY=":".join(map(str, readonly))'
    assert text.count(old) == 1
    text = text.replace(old, 'TRY_BIND_READONLY=":".join(map(str, readonly)) + ":/usr/local/bin/codex:/usr/local/bin/codex-code-mode-host"')
    entry.write_text(text)
    inventory = [{'path': str(p.relative_to(source)), 'sha256': base.digest(p)}
                 for p in sorted(source.rglob('*')) if p.is_file() and not p.is_symlink()]
    source_hash = hashlib.sha256(json.dumps(inventory, sort_keys=True).encode()).hexdigest()
    base.save(args.cohort / 'source-manifest.json', dict(sha256=source_hash, files=inventory))
    cells = json.loads((args.cohort / 'cells.json').read_text())
    for rows in cells.values():
        for row in rows:
            row['harness'] = 'codex'
    assert len(cells['main']) == 6000
    assert Counter(r['policy'] for r in cells['main']) == Counter(dict.fromkeys(POLICIES, 1200))
    assert len({r['trace_id'] for r in cells['main']}) == 400
    reg.update(protocol='agenttx.canonical400-codex-five-policy/v1',
               harnesses=['codex'], source_sha256=source_hash,
               comparison_reference=str(REFERENCE),
               scope_change='Codex on the same 400 manifests, five policies and three repeats.',
               token_budget_enforcement='Per Responses request: reserve serialized input/tools bytes plus 16384; cap output at min(32768, remaining allowance); record returned provider usage; block after unknown usage.',
               codex_binary_sha256=base.digest(Path('/home/bfq/.local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex')),
               qualification_note='Require complete coverage, unchanged canonical inputs/runtime, command audits, and complete API accounting.',
               report_interval_s=300)
    original = json.loads((REFERENCE / 'source-manifest.json').read_text())
    differences = [r['path'] for r in original['files'] if base.digest(source / r['path']) != r['sha256']]
    assert differences == ['experiments/scripts/run_system_microbenchmark.py'], differences
    base.save(args.cohort / 'reference-source-audit.json', dict(
        reference=str(REFERENCE), relocated_reference_source=str(REFERENCE_SOURCE),
        unchanged_reference_files=len(original['files']) - 1,
        changed_files=differences, added_files=additions,
        recovery_runtime_unchanged=True, generated_inputs_identical=True,
        coordinator_changes=['Codex metered entry point', 'Codex harness', 'require model canaries to pass',
                             'preserve immutable Codex executable mounts in the existing try namespace']))
    base.save(args.cohort / 'registration.json', reg)
    base.save(args.cohort / 'cells.json', cells)
    print(json.dumps(dict(cohort=str(args.cohort), frozen_source=str(source),
                          main=6000, runtime_gate=len(cells['runtime_gate']),
                          model_canary=len(cells['model_canary']))))


if __name__ == '__main__':
    main()
