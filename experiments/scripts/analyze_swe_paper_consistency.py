#!/usr/bin/env python3
"""Audit completed SWE recovery observations against the local paper draft."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

import numpy as np

ROOT=Path(__file__).resolve().parents[2]
DATA=Path('/mnt/data/agenttx-runtime')
POLICIES=['causal','ideal_oracle','clean_recovery','crash_no_rollback','temporal_checkpoint','whole_branch_abort']
USAGE=re.compile(r'usage=Usage\(completion_tokens=(\d+),prompt_tokens=(\d+),total_tokens=(\d+)')


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def bootstrap_saving(a,b,seed):
    rng=np.random.default_rng(seed)
    indices=rng.integers(0,len(a),size=(20000,len(a)))
    savings=100*(1-np.asarray(a)[indices].sum(axis=1)/np.asarray(b)[indices].sum(axis=1))
    return [float(x) for x in np.quantile(savings,[.025,.975])]


def mcnemar(a_only,b_only):
    n=a_only+b_only
    return min(1.,2*sum(math.comb(n,k) for k in range(min(a_only,b_only)+1))/2**n) if n else 1.


def audit_gold_path_hints(by, registration):
    catalog=DATA/'cache/swe_bench/lite_test_manifest.json'
    instances={r['instance_id']:r for r in json.loads(catalog.read_text())}
    counts={};examples=[]
    for policy,rs in by.items():
        injected=matched=disclosed=0
        for task,item in rs.items():
            result=item['result']
            paths=[s[6:].strip() for s in instances[task]['patch'].splitlines() if s.startswith('+++ b/')]
            actual=(result.get('injected') or {}).get('faulty_path')
            injected+=bool(actual)
            matched+=bool(actual and actual in paths)
            disclosed+=bool(result.get('fault_location_disclosed'))
            if task not in {'psf__requests-1963','sympy__sympy-11870'}:
                continue
            for trajectory in sorted((Path(item['path']).parent/'trajectories').rglob('*.traj')):
                history=json.loads(trajectory.read_text()).get('history',[])
                for index,message in enumerate(history):
                    content=message.get('content','')
                    if message.get('role')!='user' or not isinstance(content,str):
                        continue
                    begin=content.find('## AgentTX recovery handoff')
                    clean='No faulty step, source path, or recovery certificate is available' in content
                    if begin<0 and not clean:
                        continue
                    end=content.find('PENDING:',begin) if begin>=0 else -1
                    excerpt=(content[begin:end] if end>=0 else content[:650])
                    examples.append(dict(task=task,policy=policy,result=item['path'],
                        trajectory=str(trajectory),trajectory_sha256=sha(trajectory),
                        history_index=index,role='user',gold_patch_paths=paths,
                        injected_path=actual,reported_location_disclosed=bool(result.get('fault_location_disclosed')),
                        compact_invalidated_section_lists_gold_path=bool(
                            'RECOVERY-INVALIDATED' in excerpt and actual and actual in excerpt),
                        clean_explicitly_withholds_location=clean,handoff_excerpt=excerpt))
        counts[policy]=dict(completed=len(rs),injected_path_present=injected,
            injected_path_in_gold_patch=matched,reported_location_disclosed=disclosed)
    code=Path(registration['code_root'])
    files=['experiments/workloads/swe_bench_suite.py','experiments/workloads/recovery_inject.py']
    return dict(catalog=str(catalog),catalog_sha256=sha(catalog),counts=counts,examples=examples,
        frozen_code_root=str(code),frozen_files={p:sha(code/p) for p in files},
        mechanism='load_tasks selects faulty_relpath from official patch +++ b/ paths; injection records this path; causal/ideal compact handoff exposes invalidated source paths.',
        interpretation='Gold solution path metadata is used and disclosed, not evidence that gold patch contents were applied. Effects on token cost require a controlled ablation; observed performance differences alone do not quantify this confound.')


def run():
    pointer=DATA/'validation/active-swe-full-cohort.json'
    name=json.loads(pointer.read_text())['cohort']
    cohort=DATA/'results'/name
    audit_path=DATA/'validation'/('swe-api-usage-'+name+'.json')
    audit=json.loads(audit_path.read_text())
    by={p:{} for p in POLICIES};source_paths=set();phase_checks=[]
    for usage in audit['rows']:
        if not usage['usage_complete']:continue
        path=Path(usage['result']);r=json.loads(path.read_text())
        raw=(path.parent/'worker.log').read_text(errors='replace')
        matches=list(USAGE.finditer(re.sub(r'\s+','',raw)))
        replay_calls=int(r['doc_replay_tool_calls'])
        assert len(matches)==r['model_calls']+replay_calls
        replay=sum(int(m[3]) for m in matches[:replay_calls])
        main=sum(int(m[3]) for m in matches[replay_calls:])
        assert main+replay==usage['raw_total']
        item=dict(raw=usage['raw_total'],main=main,replay=replay,recorded=usage['reported'],result=r,path=str(path))
        by[r['mode']][r['task']]=item
        source_paths.add(str(path))
        phase_checks.append(dict(task=r['task'],policy=r['mode'],api_main=main,api_replay=replay,
            main_calls=r['model_calls'],replay_calls=replay_calls,success=r['success'],result=str(path)))
    groups={}
    for p,rs in by.items():
        values=list(rs.values());rows=[x['result'] for x in values]
        tokens=[x['raw'] for x in values]
        groups[p]=dict(completed=len(rows),success=sum(r['success'] for r in rows),
            tests_ok=sum(r['tests_ok'] for r in rows),planned=300,
            success_pct_of_planned=100*sum(r['success'] for r in rows)/300,
            completed_api_tokens=sum(tokens),main_api_tokens=sum(x['main'] for x in values),
            replay_api_tokens=sum(x['replay'] for x in values),mean_api_tokens=statistics.mean(tokens),
            median_api_tokens=statistics.median(tokens),p75_api_tokens=float(np.quantile(tokens,.75)),
            recorded_tokens=sum(x['recorded'] for x in values),
            api_minus_recorded=sum(x['raw']-x['recorded'] for x in values),
            replay_cells=sum(bool(r['doc_replay_needed']) for r in rows),
            median_wall_s=statistics.median(r['wall_s'] for r in rows),
            unique_source_hashes=len({r['source_sha256'] for r in rows}),
            actual_contexts=dict(Counter(r['recovery_context_variant'] for r in rows)))
    comparisons={}
    for index,p in enumerate(POLICIES[1:]):
        common=sorted(by['causal'].keys()&by[p].keys())
        both=[t for t in common if by['causal'][t]['result']['success'] and by[p][t]['result']['success']]
        causal_only=sum(by['causal'][t]['result']['success'] and not by[p][t]['result']['success'] for t in common)
        control_only=sum(by[p][t]['result']['success'] and not by['causal'][t]['result']['success'] for t in common)
        a=[by['causal'][t]['raw'] for t in both];b=[by[p][t]['raw'] for t in both]
        ratios=np.array(a)/np.array(b)
        main_a=sum(by['causal'][t]['main'] for t in both);main_b=sum(by[p][t]['main'] for t in both)
        replay_a=sum(by['causal'][t]['replay'] for t in both);replay_b=sum(by[p][t]['replay'] for t in both)
        deltas=sorted([(by[p][t]['raw']-by['causal'][t]['raw'],t) for t in both],reverse=True)
        all_a=sum(by['causal'][t]['raw'] for t in common)
        all_b=sum(by[p][t]['raw'] for t in common)
        comparisons[p]=dict(paired_completed=len(common),both_success_pairs=len(both),
            all_completed_pairs=dict(tasks=len(common),causal_tokens=all_a,control_tokens=all_b,
                saving_pct=100*(1-all_a/all_b),includes_task_failures=True,
                interpretation='Observed spend across terminal result pairs, not equal-success repair efficiency; missing/incomplete attempts are excluded here and accounted separately.'),
            causal_only_success=causal_only,control_only_success=control_only,
            success_delta_pp=100*(causal_only-control_only)/len(common),
            exact_mcnemar_two_sided_p=mcnemar(causal_only,control_only),
            saving_pct=100*(1-sum(a)/sum(b)),saving_bootstrap_95_ci=bootstrap_saving(a,b,20261019+index),
            median_pair_saving_pct=float(100*(1-np.median(ratios))),
            cheaper_tasks=sum(x<y for x,y in zip(a,b)),causal_tokens=sum(a),control_tokens=sum(b),
            causal_mean_tokens=sum(a)/len(a),control_mean_tokens=sum(b)/len(b),
            causal_main_tokens=main_a,control_main_tokens=main_b,
            causal_replay_tokens=replay_a,control_replay_tokens=replay_b,
            net_saving_tokens=sum(b)-sum(a),main_saving_tokens=main_b-main_a,
            replay_saving_tokens=replay_b-replay_a,
            savings_after_removing_largest_absolute_difference_pct=None,
            largest_advantages=[dict(task=t,saved_tokens=n) for n,t in deltas[:5]],
            largest_disadvantages=[dict(task=t,saved_tokens=n) for n,t in deltas[-5:]])
        extreme=max(both,key=lambda t:abs(by[p][t]['raw']-by['causal'][t]['raw']))
        filtered=[t for t in both if t!=extreme]
        comparisons[p]['savings_after_removing_largest_absolute_difference_pct']=100*(1-
            sum(by['causal'][t]['raw'] for t in filtered)/sum(by[p][t]['raw'] for t in filtered))
        comparisons[p]['sensitivity_removed_task']=extreme
    common=set.intersection(*(set(rs) for rs in by.values()))
    six_success=sorted(t for t in common if all(by[p][t]['result']['success'] for p in POLICIES))
    joint={p:sum(by[p][t]['raw'] for t in six_success) for p in POLICIES}
    shared=dict(tasks=len(six_success),tokens=joint,
        causal_savings_pct={p:100*(1-joint['causal']/joint[p]) for p in POLICIES if p!='causal'})
    registration=json.loads((cohort/'registration.json').read_text())
    gold_hints=audit_gold_path_hints(by,registration)
    paper=ROOT/'paper/main.tex';lines=paper.read_text().splitlines()
    findings={
        'rq2_section_line':next(i for i,s in enumerate(lines,1) if '\\subsection{RQ2:' in s),
        'outdated_five_policy_line':next(i for i,s in enumerate(lines,1) if 'new 300-task, five-policy' in s),
        'native_opportunity_caption_line':next(i for i,s in enumerate(lines,1) if '189 complete SWE-Bench' in s),
        'controlled_replay_claim_line':next(i for i,s in enumerate(lines,1) if 'work avoided 1,425 replay tokens' in s),
        'official_resolved_metric':False,
        'reason':'Runner tests FAIL_TO_PASS via repository-specific commands; PASS_TO_PASS and standard resolved grading were not run.',
        'precrash_gold_seeded_cells':sum(x['result']['precrash_progress_seeded'] for rs in by.values() for x in rs.values()),
        'source_hashes':dict(Counter(x['result']['source_sha256'] for rs in by.values() for x in rs.values())),
        'protocol_estimatand':registration['comparison_estimand']}
    report=dict(checked_at=datetime.now(timezone.utc).isoformat(),cohort=name,
        source_audit=str(audit_path),audit_sha256=sha(audit_path),paper=str(paper),paper_sha256=sha(paper),
        counts=audit['progress_counts'],complete_usage_cells=audit['cells_with_complete_logged_usage'],
        known_all_attempt_tokens=audit['all_terminal_logged_api_tokens'],
        incomplete_attempt_known_tokens=audit['incomplete_terminal_logged_api_tokens'],
        incomplete_attempt_inflight_usage_unknown=True,groups=groups,comparisons=comparisons,
        six_arm_common_success=shared,paper_findings=findings,gold_path_hint_audit=gold_hints,
        phase_split_method='Sequential worker API Usage records: replay runs first, then main; audited main model_calls and replay tool calls match every returned response. No reasoning tokens added twice.',
        inference_limits='Bootstrap resamples matched tasks in this fixed, success-selected diagnostic cohort; it does not measure repeated-model variance or establish standard SWE-bench resolved performance. Sensitivity deletion is analysis only; original outcomes and primary estimate remain unchanged.')
    out=DATA/'validation/swe-paper-comparison-20261019';out.mkdir(exist_ok=True)
    (out/'report.json').write_text(json.dumps(report,indent=2)+'\n')
    (out/'phase-accounting.json').write_text(json.dumps(phase_checks,indent=2)+'\n')
    (out/'gold-path-evidence.json').write_text(json.dumps(gold_hints,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':run()
