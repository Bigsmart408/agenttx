#!/usr/bin/env python3
"""One-shot, audited retention for ended SWE-bench cells and historical shards."""
import collections
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
from datetime import datetime, timezone

DATA = Path('/mnt/data/agenttx-runtime')
RESULTS = DATA / 'results'
AUDIT = DATA / 'validation/swe-summary-retention-20261022'
USAGE = re.compile(r'usage=Usage\(completion_tokens=(\d+),prompt_tokens=(\d+),total_tokens=(\d+)')
POLICIES = {'causal', 'ideal_oracle', 'clean_recovery', 'crash_no_rollback', 'temporal_checkpoint', 'whole_branch_abort'}

def write(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + '.retention-tmp')
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + '\n')
    tmp.replace(p)

def sha(p):
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(4*1024*1024), b''):
            h.update(block)
    return h.hexdigest()

def records(t):
    out = []
    if not t.exists(): return out
    assert not t.is_symlink()
    for p in sorted(t.rglob('*')):
        if p.is_file():
            assert not p.is_symlink()
            item = dict(path=str(p.relative_to(t)), bytes=p.stat().st_size, sha256=sha(p))
            if p.suffix == '.traj':
                try:
                    d = json.loads(p.read_text())
                    info = d.get('info', {})
                    item.update(steps=len(d.get('trajectory', [])), messages=len(d.get('history', [])),
                        statistics={k:v for k,v in info.items() if k in (
                            'model_stats', 'exit_status', 'submission', 'evaluation', 'swe_agent_version', 'swe_rex_version')})
                except (ValueError, TypeError, AttributeError) as e:
                    item['original_trajectory_parse_error']=str(e)
            out.append(item)
    return out

def accounting_audit():
    spec = importlib.util.spec_from_file_location('swe_usage', '/home/bfq/agenttx/experiments/scripts/audit_swe_token_usage.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.audit(json.loads((DATA/'validation/active-swe-full-cohort.json').read_text())['cohort'])

def audit_values(report):
    return {k:v for k,v in report.items() if k not in ('checked_at','rows')} | {
        'rows': [{k:v for k,v in x.items() if k != 'log_sha256'} for x in report['rows']]}

def main():
    AUDIT.mkdir(parents=True, exist_ok=True)
    # Refuse concurrent SWE model execution; TB workers remain unaffected.
    processes = os.popen('ps -eo args').read().splitlines()
    assert not any(re.search(r'python[^ ]* .*experiments/scripts/(run_swe_monitored_local|sweagent_native_workload)\.py', x)
                   for x in processes if 'ps -eo' not in x), 'SWE worker still running'
    cells = {}; skipped = []
    for root in sorted(RESULTS.glob('sweagent-swe-local-monitored-*')):
        progress = json.loads((root/'progress.json').read_text())
        assert not progress.get('active'), root
        for c in progress['tasks']:
            if c['state'] not in ('completed', 'infrastructure_error'): continue
            p = Path(c.get('result_path') or root/'cells'/c['task']/c['policy']/'result.json').parent
            if not p.exists(): continue
            assert p.is_relative_to(RESULTS) and c['policy'] in POLICIES
            if (p/'result.json').exists():
                d = json.loads((p/'result.json').read_text())
                assert d['suite'] == 'swe' and d['mode'] == c['policy']
            cells[str(p)] = dict(path=str(p), policy=c['policy'], state=c['state'])
    history = []
    for root in sorted(RESULTS.iterdir()):
        if not root.is_dir() or root.name.startswith(('tb-', 'terminal')): continue
        for t in sorted(root.glob('shards/*/trajectories')):
            if not any(t.rglob('*.traj')): continue
            f = t.parent/'official_tasks.json'
            try:
                rows = json.loads(f.read_text())['rows']
                modes = {r['mode'] for r in rows}; suites = {r['suite'] for r in rows}
                assert suites == {'swe'} and len(modes) == 1
                mode = modes.pop(); assert mode in POLICIES
            except Exception as e:
                skipped.append(dict(path=str(t), reason=str(e))); continue
            history.append(dict(path=str(t), policy=mode))
    plan_path=AUDIT/'plan.json'
    if plan_path.exists():
        original=json.loads(plan_path.read_text())
        history=original['historical_trajectory_roots'];skipped=original['skipped']
        assert {x['path'] for x in original['cells']}==set(cells)
    else: write(plan_path, dict(cells=list(cells.values()), historical_trajectory_roots=history, skipped=skipped))
    before_path=AUDIT/'before-usage.json'
    before = json.loads(before_path.read_text()) if before_path.exists() else accounting_audit()
    if not before_path.exists(): write(before_path, before)
    protected = {}
    for item in cells.values():
        f = Path(item['path'])/'result.json'
        if f.exists(): protected[str(f)] = sha(f)
    for h in history:
        cohort = Path(h['path']).parents[2]
        for f in cohort.rglob('*'):
            if f.is_file() and f.suffix in ('.json','.csv') and 'trajectories' not in f.parts:
                protected[str(f)] = sha(f)
    protected_path=AUDIT/'protected-results-before.json'
    if protected_path.exists():
        protected=json.loads(protected_path.read_text())
    else: write(protected_path, protected)
    counts=collections.Counter()
    removed=json.loads((AUDIT/'progress.json').read_text()).get('removed_file_bytes',0) if (AUDIT/'progress.json').exists() else 0
    for item in list(cells.values()) + [dict(x, historical=True) for x in history]:
        p=Path(item['path']); historical=item.get('historical',False)
        base=p.parent if historical else p
        marker=base/'swe-retention.json'
        if marker.exists():
            counts['clean_preserved' if item['policy']=='clean_recovery' else 'nonclean_summarized']+=1
            continue
        t=p if historical else p/'trajectories'
        full=item['policy']=='clean_recovery'
        inventory=records(t)
        write(base/'trajectory-retention-manifest.json', dict(policy=item['policy'], full_trajectory_retained=full, files=inventory))
        log=base/'worker.log'; billing=None
        if not historical and log.exists():
            raw=log.read_bytes(); matches=list(USAGE.finditer(re.sub(r'\s+', '', raw.decode(errors='replace'))))
            calls=[dict(completion_tokens=int(m[1]),prompt_tokens=int(m[2]),total_tokens=int(m[3])) for m in matches]
            result=json.loads((base/'result.json').read_text()) if (base/'result.json').exists() else {}
            replay=result.get('doc_replay_tool_calls')
            billing=dict(original_log_sha256=hashlib.sha256(raw).hexdigest(), original_log_bytes=len(raw),
                calls=calls, api_total=sum(x['total_tokens'] for x in calls),
                replay_calls=replay, api_replay=sum(x['total_tokens'] for x in calls[:replay]) if replay is not None else None,
                api_main=sum(x['total_tokens'] for x in calls[replay:]) if replay is not None else None,
                reported_main_tokens=result.get('total_tokens'), reported_replay_tokens=result.get('doc_replay_tokens'),
                usage_complete=bool(result) and bool(calls) and len(calls)==result.get('model_calls',0)+(replay or 0)
                    and all(x['prompt_tokens']+x['completion_tokens']==x['total_tokens'] for x in calls))
            write(base/'accounting-summary.json', billing)
            if not full:
                errors=[line[-1500:] for line in raw.decode(errors='replace').splitlines()
                        if re.search(r'(?i)(traceback|exception|error:|timed out|timeout)',line)]
                write(base/'failure-diagnostics.json', dict(log_error_lines=errors[-40:],
                    result_error=result.get('error'), verifier_stdout=result.get('verifier_stdout'),
                    verifier_stderr=result.get('verifier_stderr'), tests_ok=result.get('tests_ok'), success=result.get('success')))
                # Preserve numeric records compatible with the frozen accounting readers.
                tmp=log.with_name('worker.log.retention-tmp')
                tmp.write_text('AgentTX summary retention: full dialogue removed; original hash in accounting-summary.json\n'+''.join(
                    f"usage=Usage(completion_tokens={x['completion_tokens']},prompt_tokens={x['prompt_tokens']},total_tokens={x['total_tokens']})\n" for x in calls))
                removed+=max(0,len(raw)-tmp.stat().st_size);tmp.replace(log)
        if not full and t.exists():
            removed+=sum(x['bytes'] for x in inventory)
            shutil.rmtree(t)
        if full:
            assert records(t)==inventory, 'Clean trajectories changed'
        write(marker, dict(schema='agenttx.swe-summary-retention/v1', at=datetime.now(timezone.utc).isoformat(),
            policy=item['policy'], full_trajectory_retained=full, historical=historical,
            original_result_sha256=protected.get(str(base/'result.json'))))
        counts['clean_preserved' if full else 'nonclean_summarized']+=1
        with (AUDIT/'actions.jsonl').open('a') as f:f.write(json.dumps(dict(item,files=len(inventory),full_retained=full))+'\n')
        write(AUDIT/'progress.json',dict(counts=counts,removed_file_bytes=removed))
    after=accounting_audit();write(AUDIT/'after-usage.json',after)
    assert audit_values(before)==audit_values(after), 'Usage/outcomes changed'
    changed=[p for p,h in protected.items() if sha(Path(p))!=h]
    assert not changed, changed
    result=dict(finished_at=datetime.now(timezone.utc).isoformat(),counts=counts,
        removed_file_bytes=removed,removed_gib=removed/2**30, original_results_verified=len(protected),
        usage_and_outcomes_unchanged=True, skipped=skipped,
        complete_cells=after['cells_with_complete_logged_usage'],known_api_tokens=after['all_terminal_logged_api_tokens'])
    write(AUDIT/'verification.json',result);print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__': main()
