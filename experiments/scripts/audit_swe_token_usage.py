#!/usr/bin/env python3
"""Read logged API usage separately from SWE-agent's text token estimates."""
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import re
import statistics

DATA = Path('/mnt/data/agenttx-runtime')
USAGE = re.compile(r'usage=Usage\(completion_tokens=(\d+),prompt_tokens=(\d+),total_tokens=(\d+)')


def audit(cohort):
    root = DATA / 'results' / cohort
    progress = json.loads((root / 'progress.json').read_text())
    rows = []
    for cell in progress['tasks']:
        if cell['state'] not in ('completed', 'infrastructure_error'):
            continue
        path = Path(cell.get('result_path') or root / 'cells' / cell['task'] / cell['policy'] / 'result.json')
        if not path.exists():
            log=path.parent/'worker.log'
            if not log.exists():continue
            raw=log.read_bytes()
            matches=list(USAGE.finditer(re.sub(r'\s+','',raw.decode(errors='replace'))))
            rows.append(dict(task=cell['task'],policy=cell['policy'],state=cell['state'],
                success=False,result=str(path),result_sha256=None,
                log_sha256=hashlib.sha256(raw).hexdigest(),raw_usage_records=len(matches),
                expected_calls=None,raw_prompt=sum(int(m[2]) for m in matches),
                raw_completion=sum(int(m[1]) for m in matches),raw_total=sum(int(m[3]) for m in matches),
                reported=None,usage_complete=False,missing_final_result=True,
                in_flight_request_usage_unknown=True))
            continue
        result = json.loads(path.read_text())
        log = path.parent / 'worker.log'
        raw = log.read_bytes()
        # Rich wraps response representations at terminal width, including in
        # identifiers. Remove whitespace before extracting numerical usage.
        text = re.sub(r'\s+', '', raw.decode(errors='replace'))
        matches = list(USAGE.finditer(text))
        expected = result['model_calls'] + result['doc_replay_tool_calls']
        consistent = all(int(m[1]) + int(m[2]) == int(m[3]) for m in matches)
        valid = bool(matches) and len(matches) == expected and consistent
        rows.append(dict(task=cell['task'], policy=cell['policy'], state=cell['state'],
                         success=result.get('success') is True, result=str(path),
                         result_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         log_sha256=hashlib.sha256(raw).hexdigest(),
                         raw_usage_records=len(matches), expected_calls=expected,
                         raw_prompt=sum(int(m[2]) for m in matches),
                         raw_completion=sum(int(m[1]) for m in matches),
                         raw_total=sum(int(m[3]) for m in matches),
                         reported=result['total_tokens'] + result['doc_replay_tokens'],
                         usage_complete=valid))
    by = {}
    for row in rows:
        if row['state'] == 'completed' and row['usage_complete']:
            by.setdefault(row['policy'], {})[row['task']] = row
    comparisons = {}
    for policy, controls in by.items():
        if policy == 'causal':
            continue
        causal = by.get('causal', {})
        tasks = [t for t in causal.keys() & controls.keys() if causal[t]['success'] and controls[t]['success']]
        if not tasks:
            continue
        ca = sum(causal[t]['raw_total'] for t in tasks)
        co = sum(controls[t]['raw_total'] for t in tasks)
        ea = sum(causal[t]['reported'] for t in tasks)
        eb = sum(controls[t]['reported'] for t in tasks)
        comparisons[policy] = dict(both_success_pairs=len(tasks), raw_api_causal=ca, raw_api_control=co,
                                  raw_api_saving_pct=100 * (1 - ca / co),
                                  recorded_causal=ea, recorded_control=eb,
                                  recorded_saving_pct=100 * (1 - ea / eb),
                                  raw_median_pair_saving_pct=100 * (1 - statistics.median(causal[t]['raw_total'] / controls[t]['raw_total'] for t in tasks)),
                                  causal_cheaper_raw=sum(causal[t]['raw_total'] < controls[t]['raw_total'] for t in tasks))
    return dict(checked_at=datetime.now(timezone.utc).isoformat(), cohort=cohort,
                progress_counts=progress['counts'], coverage=len(rows),
                cells_with_complete_logged_usage=sum(r['usage_complete'] for r in rows),
                cells_with_missing_final_result=sum(r.get('missing_final_result',False) for r in rows),
                all_terminal_logged_api_tokens=sum(r['raw_total'] for r in rows),
                incomplete_terminal_logged_api_tokens=sum(r['raw_total'] for r in rows if not r['usage_complete']),
                definition='Sum logged API prompt_tokens + completion_tokens across main and replay; reasoning is already within completion, never added twice.',
                limitations='Offline debug-log reconstruction, call counts and sums checked; not an independently reconciled provider invoice. Incomplete cells excluded from API comparisons.',
                by_policy={p:dict(completed=len(rs), success=sum(r['success'] for r in rs.values()),
                                  raw_api_tokens=sum(r['raw_total'] for r in rs.values()),
                                  recorded_tokens=sum(r['reported'] for r in rs.values())) for p,rs in by.items()},
                paired_success_comparisons=comparisons, rows=rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cohort')
    args = parser.parse_args()
    cohort = args.cohort or json.loads((DATA / 'validation/active-swe-full-cohort.json').read_text())['cohort']
    assert Path(cohort).name == cohort and cohort.startswith('sweagent-swe-local-monitored-')
    report = audit(cohort)
    out = DATA / 'validation' / ('swe-api-usage-' + cohort + '.json')
    temporary = out.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, indent=2) + '\n')
    temporary.replace(out)
    print(json.dumps({k: v for k, v in report.items() if k != 'rows'}, indent=2))


if __name__ == '__main__':
    main()
