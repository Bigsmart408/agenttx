#!/usr/bin/env python3
"""Codex continuation over the unchanged canonical-400 recovery benchmark."""
from __future__ import annotations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from experiments.scripts import bench_runtime_recovery_system as legacy
from experiments.scripts.codex_micro_usage import metered_codex


class MeteredCodexHarness(legacy.CodexHarness):
    def run_in_transaction(self, harness, task):
        output = harness.workdir.parent
        with metered_codex(self, output / 'api-usage.json', 8_000_000):
            result = super().run_in_transaction(harness, task)
        return result


original_cell = legacy.run_cell


def run_cell(args, plan, policy, adapter, output):
    row = original_cell(args, plan, policy, adapter, output)
    if not args.runtime_only:
        path = output / 'api-usage.json'
        usage = json.loads(path.read_text()) if path.exists() else dict(
            source='api_response_usage', complete=False, total_tokens=None,
            known_total_tokens=0, requests=[], budget_exceeded=False)
        if not usage.get('requests'):
            usage.update(complete=False, total_tokens=None,
                         startup_error='Codex made no metered API request')
        row['harness_reported_token_usage'] = {k: row.get(k) for k in
            ('prompt_tokens', 'completion_tokens', 'total_tokens', 'usage_source')}
        row['api_usage'] = usage
        row['model_calls'] = len(usage.get('requests', []))
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            row[key] = usage.get(key)
        row['usage_source'] = usage['source']
        (output / 'result.json').write_text(json.dumps(row, indent=2) + '\n')
    return row


if __name__ == '__main__':
    legacy.CodexHarness = MeteredCodexHarness
    legacy.run_cell = run_cell
    raise SystemExit(legacy.main())
