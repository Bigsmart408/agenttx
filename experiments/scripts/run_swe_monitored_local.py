#!/usr/bin/env python3
"""Local, frozen SWE-agent launch with canary gates and durable attempt records."""
from collections import Counter
from datetime import datetime, timezone
import argparse
import csv
import fcntl
import hashlib
from itertools import zip_longest
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]
from experiments.scripts.experiment_provenance import source_manifest
from experiments.scripts.experiment_credentials import private_values, redact_credentials, purge_private_codex_auth
DATA=Path('/mnt/data/agenttx-runtime')
RUN=os.environ.get('AGENTTX_SWE_MONITORED_RUN','sweagent-swe-local-monitored-20261018-r3')
if Path(RUN).name != RUN or not RUN.startswith('sweagent-swe-local-monitored-'):
    raise ValueError('invalid monitored cohort name')
OUT=DATA/'results'/RUN
CACHE=DATA/'cache/swe-monitored-20261018'
VERIFIER_CACHE=DATA/'cache/swe-verifier-public-20261018'
BASE_POLICIES=('clean_recovery','crash_no_rollback','temporal_checkpoint','whole_branch_abort','causal')
POLICIES=BASE_POLICIES+(('ideal_oracle',) if os.environ.get('AGENTTX_SWE_INCLUDE_IDEAL')=='1' else ())
UID=1003

def context_by_policy(environ=None):
    """Resolve an explicit treatment profile; never infer it from arm names alone."""
    env=os.environ if environ is None else environ
    profile=env.get('AGENTTX_SWE_CONTEXT_PROFILE','uniform')
    if profile=='selective-compact-v1':
        assert 'ideal_oracle' in POLICIES, 'selective profile requires all six policies'
        return {p:('compact' if p in ('causal','ideal_oracle','clean_recovery') else 'full') for p in POLICIES}
    if profile!='uniform':raise ValueError('unknown SWE context profile: '+profile)
    context=env.get('AGENTTX_RECOVERY_CONTEXT','full')
    if context not in ('full','compact'):raise ValueError('context must be full or compact')
    return {p:context for p in POLICIES}

def worker_command(*arguments):
    """Use a data-backed /tmp alias visible through try's subtree overlays."""
    bwrap='/home/bfq/agenttx-deps/bubblewrap/usr/bin/bwrap'
    readonly=[str(CACHE/'swe_bench/venvs'),str(DATA/'tiktoken-cache'),str(VERIFIER_CACHE)]
    return [bwrap,'--bind','/','/','--dev-bind','/dev','/dev','--proc','/proc',
            '--tmpfs','/root','--setenv','HOME','/root','--bind',str(DATA/'tmp'),'/tmp',
            '--setenv','TMPDIR','/tmp','--setenv','TEMP','/tmp','--setenv','TMP','/tmp',
            '--setenv','AGENTTX_TRACE_SPOOL_ROOT','/tmp',
            '--setenv','TRY_BIND_READONLY',':'.join(readonly),
            '--setenv','TRY_BIND_SOCKETS','/run/agenttx-docker/docker.sock',
            '--setenv','AGENTTX_SWE_VERIFIER_CACHE',str(VERIFIER_CACHE),
            '--setenv','AGENTTX_MOTIVATION_BWRAP','1',*arguments]

def digest_file(path):return hashlib.sha256(path.read_bytes()).hexdigest()

def now():return datetime.now(timezone.utc).isoformat()
def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp')
    tmp.write_text(json.dumps(value,indent=2)+'\n');tmp.replace(path)
    if os.geteuid()==0:os.chown(path,UID,UID)

def hydrate(task,image,base):
    """Recover source from an existing pinned image, never from recovered partial trees."""
    from experiments.workloads import swe_bench_suite as swe
    def prepare_tests():
        # The coordinator initializes the shared cache before any policy worker.
        # Workers only copy the now-complete tree; no parallel git-apply race.
        swe.ensure_repo(swe.load_tasks(CACHE)[task], CACHE)
    repo=CACHE/'swe_bench/repos'/task
    marker=repo.parent/(task+'.source-verified.json')
    if marker.exists():
        proof=json.loads(marker.read_text())
        assert proof['image_id']==image and proof['base_commit']==base
        assert subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()==base
        prepare_tests()
        return
    if repo.exists():
        # A first-task source was hydrated and test-patched before registration.
        assert subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()==base
    else:
        repo.mkdir(parents=True)
        cid=subprocess.check_output(['docker','create','--pull=never','--network=none',image,'true'],text=True).strip()
        try:subprocess.run(['docker','cp',cid+':/testbed/.',str(repo)],check=True,timeout=300)
        finally:subprocess.run(['docker','rm','-f',cid],capture_output=True,timeout=30)
        subprocess.run(['git','-C',str(repo),'reset','--hard',base],check=True,capture_output=True)
        subprocess.run(['git','-C',str(repo),'clean','-fdx'],check=True,capture_output=True)
        assert subprocess.check_output(['git','-C',str(repo),'status','--porcelain'],text=True)==''
    save(marker,dict(task=task,image_id=image,base_commit=base,checked_at=now()))
    prepare_tests()

def cell(task,policy):
    # Set this inside each independent worker before prompt construction and
    # native-context loading. A registration label alone does not change either.
    expected=context_by_policy()[policy]
    os.environ['AGENTTX_RECOVERY_CONTEXT']=expected
    from experiments.scripts import bench_official_tasks as bench
    from experiments.workloads import swe_bench_suite as swe
    folder=OUT/'cells'/task/policy
    row=bench.run_once(suite='swe',task_name=task,mode=policy,repeat=0,cache_root=CACHE,
        model='gpt-5.6-luna',provider=None,max_turns=None,trace_backend='strace',python=sys.executable,
        oracle=False,harness_backend='sweagent',harness_root=Path(os.environ['AGENTTX_SWE_AGENT_ROOT']),
        harness_timeout_s=900,harness_output_dir=folder,reasoning_effort='max',swe_tasks=swe.load_tasks(CACHE))
    # A model/task failure is retained and is distinct from a launch failure.
    row.update(formal_eligible=False,cohort=RUN,source_sha256=source_manifest(ROOT)['sha256'],
               context_profile=os.environ.get('AGENTTX_SWE_CONTEXT_PROFILE','uniform'),
               expected_recovery_context_variant=expected)
    secrets=private_values()
    row=redact_credentials(row,secrets)
    save(folder/'result.json',row)
    cleanup=purge_private_codex_auth(folder,secrets)
    if cleanup:save(folder/'auth-cleanup.json',cleanup)
    for path in [folder,*folder.rglob('*')]:
        if not path.is_symlink():
            try:os.chown(path,UID,UID)
            except OSError:pass
    print(json.dumps({k:row.get(k) for k in ['task','mode','success','environment_error','error','total_tokens','tests_rc']}),flush=True)

def session_timeout_audit(folder):
    """Prove a registered 900-second timeout despite SWE-agent's exit_error label."""
    result_path=folder/'result.json'
    if not result_path.exists():return None
    row=json.loads(result_path.read_text())
    if not (row.get('harness_returncode')==1
            and row.get('harness_stderr')=='SWEAgent exit_status=exit_error'
            and row.get('environment_error') is False and row.get('success') is False
            and row.get('finished') is False
            and row.get('error')=='RecoveryProtectionError: external session did not finish; refusing to commit its partial sandbox'
            and 900<=float(row.get('official_model_wall_s') or 0)<=960):return None
    log=folder/'worker.log'
    if not log.exists():return None
    compact=re.sub(r'\s+','',log.read_text(errors='replace'))
    if '_SWEAgentWallTimeout:SWEAgentworkloadexceededitswall-clocktimeout' not in compact:return None
    records=[]
    message='Exit due to unknown error: SWEAgent workload exceeded its wall-clock timeout'
    for path in sorted((folder/'trajectories').rglob('*.traj')):
        trajectory=json.loads(path.read_text())
        steps=trajectory.get('trajectory',[])
        info=trajectory.get('info',{})
        stats=info.get('model_stats',{})
        if (steps and steps[-1].get('response')==message and not steps[-1].get('action')
                and info.get('exit_status')=='exit_error'
                and stats.get('api_calls')==row.get('model_calls')
                and stats.get('tokens_sent')==row.get('prompt_tokens')
                and stats.get('tokens_received')==row.get('completion_tokens')):
            records.append(dict(trajectory=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
    if len(records)!=1:return None
    return dict(classification='registered_session_timeout',registered_limit_s=900,
                actual_main_wall_s=row['official_model_wall_s'],trajectories=records,
                log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),task_retried=False)


def verifier_command_audit(folder, expected_image):
    """Recognize an agent-altered image argument from actual tool episodes.

    A bad model command remains charged to its task. Only the narrowly proven
    argument error is exempted; genuine evaluator failures still stop dispatch.
    """
    if not re.fullmatch(r'sha256:[0-9a-f]{64}',expected_image):return None
    episodes=[]
    kinds=set()
    for path in sorted((folder/'trajectories').rglob('*.traj')):
        trajectory=json.loads(path.read_text())
        for index,step in enumerate(trajectory.get('trajectory',[])):
            observation=str(step.get('observation',''))
            if 'Official evaluator infrastructure error' not in observation:continue
            signature_lines=[line for line in observation.splitlines() if 'Official evaluator infrastructure error' in line]
            source_literal='print(f"Official evaluator infrastructure error: {type(exc).__name__}: {exc}", file=sys.stderr)'
            action=str(step.get('action',''))
            # Reading the verifier's exception handler is ordinary tool output,
            # not an exception raised by the evaluator. Check every occurrence
            # so a genuine error alongside the source quotation is not hidden.
            if (signature_lines and all(source_literal in line for line in signature_lines)
                    and 'verify_swe_workspace.py' in action
                    and re.match(r'^\s*(?:sed|cat|head|tail|grep|rg)\b',action)):
                episodes.append(dict(trajectory=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                     step=index,kind='verifier_source_literal'))
                kinds.add('verifier_source_literal')
                continue
            try:arguments=shlex.split(str(step.get('action','')))
            except ValueError:return None
            if arguments.count('--image-id')!=1:return None
            offset=arguments.index('--image-id')+1
            if offset>=len(arguments):return None
            supplied=arguments[offset]
            identity_message='Official evaluator infrastructure error: RuntimeError: official evaluator image identity mismatch'
            image_argument_error=('No such image: '+supplied in observation or
                                  (signature_lines and all(line.strip()==identity_message for line in signature_lines)))
            if not (re.fullmatch(r'sha256:[0-9a-f]{1,128}',supplied)
                    and supplied!=expected_image
                    and 'verify_swe_workspace.py' in str(step.get('action',''))
                    and image_argument_error):return None
            episodes.append(dict(trajectory=str(path),sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                                 step=index,supplied_image=supplied))
            kinds.add('agent_authored_invalid_image_id')
    timeout=session_timeout_audit(folder)
    if not episodes and not timeout:return None
    # Prove the registered image exists in the authoritative daemon.
    inspected=subprocess.run(['docker','image','inspect','--format','{{.Id}}',expected_image],
                             capture_output=True,text=True,timeout=20)
    if inspected.returncode or inspected.stdout.strip()!=expected_image:return None
    classification=(next(iter(kinds)) if len(kinds)==1 else
                    'agent_command_and_source_literal' if kinds else 'registered_session_timeout')
    return dict(classification=classification,expected_image=expected_image,
                correct_image_present=True,episodes=episodes,session_timeout=timeout,task_retried=False)


def infrastructure_issue(row, worker_log='', expected_context=None, command_audit=None):
    if expected_context is not None and row.get('recovery_context_variant')!=expected_context:return 'recovery_context_mismatch'
    if row.get('environment_error'):return 'environment_error'
    if row.get('harness_returncode')==127:return 'harness_unavailable'
    evidence='\n'.join(str(row.get(key) or '') for key in ('error','harness_stderr','verifier_stderr'))+'\n'+worker_log
    for signature,reason in (
        ('Cannot connect to the Docker daemon','sandbox_docker_unavailable'),
        ('Official evaluator infrastructure error','agent_verifier_infrastructure_error'),
        ('strace did not produce dependency output','dependency_trace_missing'),
        ('SWEAgent exit_status=exit_error','harness_integration_error'),
    ):
        if signature in evidence:
            if (reason=='harness_integration_error' and command_audit
                    and command_audit.get('correct_image_present')
                    and command_audit.get('session_timeout')
                    and command_audit['session_timeout'].get('classification')=='registered_session_timeout'
                    and row.get('success') is False and row.get('harness_returncode')==1
                    and type(row.get('tests_rc')) is int):continue
            if (reason=='agent_verifier_infrastructure_error' and command_audit
                    and command_audit.get('classification') in {'agent_authored_invalid_image_id','verifier_source_literal','agent_command_and_source_literal'}
                    and command_audit.get('correct_image_present') and command_audit.get('episodes')
                    and type(row.get('tests_rc')) is int):continue
            return reason
    if row.get('usage_source') in (None,'','none') or int(row.get('total_tokens') or 0)<=0:return 'missing_model_usage'
    if row.get('tests_rc') in (None,'error'):return 'official_verifier_missing'
    if row.get('host_leak_before_commit'):return 'host_write_leak'
    if row.get('fault_location_disclosed') and not (
        (row.get('mode')=='causal' and row.get('fault_location_source')=='agenttx_localizer')
        or (row.get('mode')=='ideal_oracle' and row.get('fault_location_source')=='oracle'
            and row.get('oracle_fault_step_disclosed') is True and row.get('localization_validation')=='oracle_truth')
    ):return 'fault_location_disclosed'
    if row.get('mode')!='clean_recovery' and not row.get('recovery_manifest_authoritative'):return 'recovery_state_not_authoritative'
    return None

def full_suite_gate(proof_path, root, redirect_proof=None, large_write_proof=None):
    """Reuse the completed live canary only when its execution core is unchanged."""
    proof=json.loads(proof_path.read_text())
    expected=context_by_policy()
    assert proof.get('infrastructure_valid',proof.get('passed'))==proof['attempts']==len(POLICIES)
    assert {r['policy'] for r in proof['rows']}==set(POLICIES)
    for record in proof['rows']:
        path=Path(record['result'])
        assert hashlib.sha256(path.read_bytes()).hexdigest()==record['sha256']
        row=json.loads(path.read_text())
        assert infrastructure_issue(row,expected_context=expected[record['policy']]) is None
    old=DATA/'results'/proof['cohort']/'source-manifest.json'
    before=json.loads(old.read_text())
    after={r['path']:r['sha256'] for r in source_manifest(root)['files']}
    orchestration_only={'experiments/scripts/run_swe_monitored_local.py',
                        'experiments/scripts/launch_swe_selective_profile.py',
                        'scripts/launch-local-swe-selective-profile-20261018.sh',
                        'experiments/scripts/watch_local_campaigns.py',
                        'experiments/workloads/terminal_bench_suite.py'}
    if redirect_proof:
        assert redirect_proof['ok'] and redirect_proof['model_calls']==0 and not redirect_proof['gold_patch_used']
        assert redirect_proof['http_fixture_image']==os.environ['AGENTTX_HTTPBIN_IMAGE']
        assert redirect_proof['swe_module_sha256']==after['experiments/workloads/swe_bench_suite.py']
        checks={r['check']:r for r in redirect_proof['checks']}
        assert checks['psf__requests-2148-base']['issue_test_only']
        assert checks['psf__requests-2148-prior-model']['tests_rc']==0
        assert checks['psf__requests-1963-prior-model']['tests_rc']==0
        orchestration_only.add('experiments/workloads/swe_bench_suite.py')
    if large_write_proof:
        assert large_write_proof['valid'] and large_write_proof['model_calls']==0 and not large_write_proof['gold_patch_used']
        assert large_write_proof['task']=='matplotlib__matplotlib-22711'
        assert large_write_proof['semisolate_sha256']==after['src/agenttx/semisolate.py']
        assert all(large_write_proof[k] for k in ('fault_write_succeeded','original_source_restored','independent_documents_retained','derived_removed'))
        assert large_write_proof['rollback_targets']==large_write_proof['injection']['oracle_invalid_steps']
        orchestration_only.add('src/agenttx/semisolate.py')
    before_files={r['path']:r['sha256'] for r in before['files']}
    changed=[p for p in before_files.keys()|after.keys()
             if p not in orchestration_only and after.get(p)!=before_files.get(p)]
    assert not changed, 'canary execution core changed: '+str(changed)
    return dict(path=str(proof_path),sha256=hashlib.sha256(proof_path.read_bytes()).hexdigest(),
                gate='infrastructure_and_context_only',task_success_required=False,
                cohort=proof['cohort'],execution_core_unchanged=not bool(redirect_proof),
                verified_redirect_environment_repair=bool(redirect_proof),verified_large_write_repair=bool(large_write_proof))


def inherit_terminal_cells(cells, previous, previous_root, invalid_tasks, invalid_cells=()):
    """Carry valid outcomes forward by reference, never retry a task failure."""
    old={(r['task'],r['policy']):r for r in previous['tasks']}
    inherited=0
    for row in cells:
        prior=old.get((row['task'],row['policy']),{})
        if row['task'] in invalid_tasks or (row['task'],row['policy']) in invalid_cells or prior.get('state') not in ('completed','infrastructure_error'):
            continue
        row.update(prior)
        row.update(inherited_from=previous_root.name,
                   result_path=prior.get('result_path') or str(previous_root/'cells'/row['task']/row['policy']/'result.json'))
        inherited+=1
    return inherited


def launch_window(full_suite, next_cell, cells, canary_count=None):
    if full_suite:
        return 8,len(cells)
    canary_count=len(POLICIES) if canary_count is None else canary_count
    limit=1 if next_cell==0 or cells[0]['state']=='running' else min(8,canary_count-1)
    ceiling=1 if cells[0]['state']!='completed' else canary_count
    return limit,ceiling

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cell',nargs=2)
    parser.add_argument('--full-suite',action='store_true',help='User-authorized 1500-cell comparison cohort after the verified live canary')
    parser.add_argument('--resume-all-valid',action='store_true',help='Resume unchanged protocol, preserving every terminal result')
    parser.add_argument('--repair-large-write',action='store_true',help='Use verified argument-length repair and repeat only the audited pre-model injection errors')
    parser.add_argument('--continue-from',help='Continue pending cells and the independently diagnosed invalid task; retain all other outcomes by reference')
    parser.add_argument('--verifier-command-audit',type=Path,help='Preserve a terminal result with independently audited agent-authored command errors')
    parser.add_argument('--repo-bootstrap-repair-proof',type=Path,help='Repeat only audited zero-token shared test-patch initialization failures')
    args=parser.parse_args()
    assert socket.gethostname()=='r760-ssd'
    assert os.environ['DOCKER_HOST']=='unix:///run/agenttx-docker/docker.sock'
    if args.cell:cell(*args.cell);return
    assert os.geteuid()==0
    lock=(DATA/'validation/local-swe-monitored.lock').open('a');fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    proof_path=DATA/'validation/swe-workspace-mount-preflight-20261018-r4.json'
    proof=json.loads(proof_path.read_text())
    assert proof['ok'] and 'exact_agent_verifier_prior_model_patch_passes_all_7' in proof['checks']
    redirect_path=Path(os.environ.get('AGENTTX_SWE_REDIRECT_PROOF',str(DATA/'validation/swe-redirect-preflight-20261018-r3/proof.json')))
    runtime_redirect=json.loads(redirect_path.read_text())
    assert runtime_redirect['ok'] and runtime_redirect['model_calls']==0 and not runtime_redirect['gold_patch_used']
    assert runtime_redirect['http_fixture_image']==os.environ['AGENTTX_HTTPBIN_IMAGE']
    redirect_proof=runtime_redirect if args.continue_from else None
    assert proof['http_fixture_image']==os.environ['AGENTTX_HTTPBIN_IMAGE'] or runtime_redirect['ok']
    contexts=context_by_policy()
    context_profile=os.environ.get('AGENTTX_SWE_CONTEXT_PROFILE','uniform')
    sympy_proof_path=os.environ.get('AGENTTX_SWE_SYMPY_PROOF')
    if context_profile=='selective-compact-v1':
        assert sympy_proof_path, 'selective launch requires the repaired SymPy verifier proof'
        sympy_proof=json.loads(Path(sympy_proof_path).read_text())
        assert sympy_proof['ok'] and sympy_proof['model_calls']==0 and not sympy_proof['gold_patch_used']
        assert sympy_proof['swe_module_sha256']==hashlib.sha256((ROOT/'experiments/workloads/swe_bench_suite.py').read_bytes()).hexdigest()
        assert runtime_redirect['swe_module_sha256']==sympy_proof['swe_module_sha256']
    canary_proof=Path(os.environ.get('AGENTTX_SWE_CANARY_PROOF',str(DATA/'validation/swe-local-launch-success-20261018.json')))
    large_write_path=DATA/'validation/swe-injection-probe-20261018-r3/proof.json'
    large_write_proof=json.loads(large_write_path.read_text()) if args.repair_large_write else None
    assert not args.repair_large_write or (args.continue_from and args.resume_all_valid)
    passed_canary=full_suite_gate(canary_proof,ROOT,redirect_proof,large_write_proof) if args.full_suite else None
    OUT.mkdir(exist_ok=False)
    os.chown(OUT,UID,UID)
    evidence=json.loads((DATA/'validation/environment-preparation-local-20261017-r3/execution.json').read_text())
    images={r['task']:r for r in evidence['tasks']};assert len(images)==300 and all(r['state']=='image_ready' for r in images.values())
    canary='psf__requests-1963'
    families={}
    for task in sorted(images):
        if task!=canary:families.setdefault(task.split('__')[0],[]).append(task)
    tasks=[canary]+[t for group in zip_longest(*families.values()) for t in group if t]
    cells=[dict(task=t,policy=p,state='pending') for t in tasks for p in POLICIES]
    continuation=None
    if args.continue_from:
        assert args.full_suite and Path(args.continue_from).name==args.continue_from and args.continue_from.startswith('sweagent-swe-local-monitored-')
        previous_root=DATA/'results'/args.continue_from
        assert (previous_root/'execution.json').exists(), 'prior cohort must finish draining'
        previous=json.loads((previous_root/'progress.json').read_text())
        previous_registration=json.loads((previous_root/'registration.json').read_text())
        assert previous_registration['model_wall_timeout_per_session_s']==900
        assert previous_registration['fault_seed']=='20261018'
        assert previous_registration['policies'] in (list(BASE_POLICIES),list(POLICIES))
        for prior in previous['tasks']:
            if prior['state'] in ('completed','infrastructure_error'):
                prior_path=Path(prior.get('result_path') or previous_root/'cells'/prior['task']/prior['policy']/'result.json')
                if prior_path.exists():assert json.loads(prior_path.read_text()).get('recovery_context_variant')==contexts[prior['policy']], 'cannot pool different per-policy context profiles'
        invalid_cells=set()
        if args.repair_large_write:
            audit=json.loads((previous_root/'environment-audit.json').read_text())
            assert audit['cause']=='E2BIG_before_model_fault_write'
            assert audit['repair_proof_sha256']==hashlib.sha256(large_write_path.read_bytes()).hexdigest()
            invalid_cells={(r['task'],r['policy']) for r in audit['environment_invalid_cells']}
            assert invalid_cells=={('matplotlib__matplotlib-22711',p) for p in ('crash_no_rollback','temporal_checkpoint','whole_branch_abort','causal')}
            for task,policy in invalid_cells:
                failed=json.loads((previous_root/'cells'/task/policy/'result.json').read_text())
                assert failed['environment_error'] and failed['total_tokens']==0 and 'invalid recovery DAG' in failed['error']
        if args.repo_bootstrap_repair_proof:
            repair=json.loads(args.repo_bootstrap_repair_proof.read_text())
            assert args.resume_all_valid and repair['cohort']==args.continue_from
            assert repair['cause']=='parallel_shared_test_patch_initialization' and repair['verified']
            for record in repair['cells']:
                failed_path=previous_root/'cells'/record['task']/record['policy']/'result.json'
                assert digest_file(failed_path)==record['result_sha256']
                failed=json.loads(failed_path.read_text())
                assert failed['environment_error'] and failed['total_tokens']==0 and failed['model_calls']==0
                assert failed['official_model_wall_s']==0 and "git', 'apply'" in failed['error']
                repo=CACHE/'swe_bench/repos'/record['task']
                assert digest_file(repo/'.agenttx_test_patch')==record['marker_sha256']
                instance=__import__('experiments.workloads.swe_bench_suite',fromlist=['fetch_instance']).fetch_instance(record['task'],CACHE)
                checked=subprocess.run(['git','apply','--reverse','--check','-'],cwd=repo,input=instance['test_patch'],text=True,capture_output=True)
                assert checked.returncode==0, checked.stderr
                invalid_cells.add((record['task'],record['policy']))
        inherited=inherit_terminal_cells(cells,previous,previous_root,set() if args.resume_all_valid else {'psf__requests-2148'},invalid_cells)
        if args.verifier_command_audit:
            assert args.resume_all_valid
            audit=json.loads(args.verifier_command_audit.read_text())
            assert audit['cohort']==args.continue_from
            for record in audit.get('cells',[audit]):
                audited=next(r for r in cells if (r['task'],r['policy'])==(record['task'],record['policy']))
                result_path=Path(audited['result_path'])
                assert hashlib.sha256(result_path.read_bytes()).hexdigest()==record['result_sha256']
                result=json.loads(result_path.read_text())
                image=images[audited['task']]['image_id']
                evidence=verifier_command_audit(result_path.parent,image)
                assert evidence and infrastructure_issue(result,(result_path.parent/'worker.log').read_text(errors='replace'),contexts[audited['policy']],evidence) is None
                audited.update(state='completed',infrastructure_issue=None,
                               original_infrastructure_issue=audited.get('infrastructure_issue'),
                               verifier_command_audit=str(args.verifier_command_audit))
        continuation=dict(previous_cohort=args.continue_from,inherited_terminal_cells=inherited,
                          environment_invalid_task_repeated_across_all_policies=None if args.resume_all_valid else 'psf__requests-2148',
                          unchanged_protocol_resume=args.resume_all_valid,
                          added_policies=[p for p in POLICIES if p not in previous_registration['policies']],
                          environment_invalid_cells_repeated=[dict(task=t,policy=p) for t,p in sorted(invalid_cells)],
                          repo_bootstrap_repair_proof=str(args.repo_bootstrap_repair_proof) if args.repo_bootstrap_repair_proof else None,
                          large_write_repair_proof=str(large_write_path) if args.repair_large_write else None,
                          task_failures_retried=False,redirect_preflight=str(redirect_path),
                          verifier_command_audit=str(args.verifier_command_audit) if args.verifier_command_audit else None,
                          redirect_preflight_sha256=hashlib.sha256(redirect_path.read_bytes()).hexdigest())
    registration=dict(registered_at=now(),host=socket.gethostname(),code_root=str(ROOT),harness='sweagent',
        model='gpt-5.6-luna',reasoning_effort='max',model_endpoint='http://10.249.44.127:8080/v1',
        task_count=300,cell_count=len(cells),policies=POLICIES,repeats=1,max_workers=8,
        scheduled_cell_limit=len(cells) if args.full_suite else len(POLICIES),automatic_full_suite_launch=args.full_suite,
        user_authorized_full_suite=args.full_suite,passed_canary=passed_canary,
        purpose='full-suite historical comparison' if args.full_suite else 'live launch validation',
        continuation=continuation,
        ideal_canary=dict(task=canary,enabled='ideal_oracle' in POLICIES,gate='validated with all six arms; no success-based or serial ideal gate'),
        canary=dict(task=canary,cells=len(POLICIES),gate='infrastructure and usage validity, independent of task success'),
        model_wall_timeout_per_session_s=900,cell_wall_timeout_s=3600,max_steps=None,max_model_calls=None,
        token_budget=None,formal_eligible=False,
        remaining_formal_gates=['shared hard token budget','matched prompt/policy payload accounting','cross-harness protocol validation'],
        context_variant=next(iter(contexts.values())) if len(set(contexts.values()))==1 else 'per_policy',
        context_profile=context_profile,context_by_policy=contexts,
        comparison_estimand='recovery policy plus registered context treatment; not rollback-only attribution',
        sympy_verifier_proof=sympy_proof_path,
        redirect_preflight=str(redirect_path),
        randomize_crash=True,fault_seed='20261018',gold_patch_used=False,
        http_fixture_image=os.environ.get('AGENTTX_HTTPBIN_IMAGE'),
        runtime_preflight_proof_sha256=hashlib.sha256(proof_path.read_bytes()).hexdigest(),
        public_verifier_metadata_sha256=hashlib.sha256((VERIFIER_CACHE/'swe_bench/lite_test_manifest.json').read_bytes()).hexdigest(),
        agent_runtime='host isolated mount namespace',verifier_runtime='pinned official Docker image',
        automatic_outcome_retry=False,source_sha256=source_manifest(ROOT)['sha256'],images=[dict(task=t,image_id=images[t]['image_id'],base_commit=images[t]['base_commit']) for t in tasks])
    save(OUT/'registration.json',registration);save(OUT/'source-manifest.json',source_manifest(ROOT))
    with (OUT/'cells.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=['task','policy','state'],extrasaction='ignore');writer.writeheader();writer.writerows(cells)
    if args.full_suite:save(DATA/'validation/active-swe-full-cohort.json',dict(cohort=RUN,activated_at=now(),continuation=continuation))
    active=[];next_cell=0;stop_reason=None;last_report=0
    def report():
        save(OUT/'progress.json',dict(updated_at=now(),coordinator_pid=os.getpid(),
            counts=dict(Counter(r['state'] for r in cells)),active=[dict(task=r['task'],policy=r['policy'],pid=p.pid) for r,p,_ in active],
            stage='attention_required' if stop_reason else 'running_full_suite' if args.full_suite else 'canary',stop_reason=stop_reason,
            inherited_terminal_cells=sum(bool(r.get('inherited_from')) for r in cells),
            formal_eligible=False,tasks=cells,disk_free_gib=round(shutil.disk_usage(DATA).free/2**30,1)))
    def stop(sig,frame):
        nonlocal stop_reason
        stop_reason='received '+signal.Signals(sig).name
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    while next_cell<len(cells) or active:
        for item in list(active):
            row,process,log=item
            if process.poll() is None:continue
            log.close();active.remove(item)
            path=OUT/'cells'/row['task']/row['policy']/'result.json'
            result=json.loads(path.read_text()) if path.exists() else {}
            worker_log=(path.parent/'worker.log').read_text(errors='replace')
            needs_audit=('Official evaluator infrastructure error' in worker_log
                         or result.get('harness_stderr')=='SWEAgent exit_status=exit_error')
            command_audit=verifier_command_audit(path.parent,images[row['task']]['image_id']) if needs_audit else None
            if command_audit:save(path.parent/'verifier-command-audit.json',command_audit)
            issue=infrastructure_issue(result,worker_log,expected_context=contexts[row['policy']],command_audit=command_audit) if result else 'worker_terminated_without_result'
            row.update(state='infrastructure_error' if issue else 'completed',finished_at=now(),
                       success=result.get('success'),returncode=process.returncode,infrastructure_issue=issue,
                       tokens=result.get('total_tokens'),error=result.get('error'))
            if issue and not stop_reason:stop_reason=row['task']+':'+row['policy']+':'+issue
            report()
        if shutil.disk_usage(DATA).free<200*2**30 and not stop_reason:stop_reason='data_disk_below_200_gib_guard'
        if not args.full_suite and all(r['state']=='completed' for r in cells[:len(POLICIES)]) and not stop_reason:
            stop_reason='canary_complete_formal_protocol_gates_pending'
        if stop_reason:
            # Finish already-running cells, but never add work after a launch fault.
            if not active:break
        else:
            # First clean cell alone, then every registered canary arm.
            limit,ceiling=launch_window(args.full_suite,next_cell,cells)
            while len(active)<limit and next_cell<ceiling:
                if cells[next_cell]['state']!='pending':next_cell+=1;continue
                row=cells[next_cell];image=images[row['task']]
                try:hydrate(row['task'],image['image_id'],image['base_commit'])
                except Exception as exc:
                    row.update(state='infrastructure_error',error=str(exc));stop_reason='source_hydration_failed';report();break
                folder=OUT/'cells'/row['task']/row['policy'];folder.mkdir(parents=True,exist_ok=False)
                log=(folder/'worker.log').open('w')
                command=worker_command(sys.executable,'-u',str(Path(__file__).resolve()),'--cell',row['task'],row['policy'])
                command=['timeout','--signal=TERM','--kill-after=30','3600',*command]
                worker_env=dict(os.environ,AGENTTX_RECOVERY_CONTEXT=contexts[row['policy']])
                process=subprocess.Popen(command,cwd=ROOT,env=worker_env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                row.update(state='running',started_at=now(),pid=process.pid,expected_context=contexts[row['policy']]);active.append((row,process,log));next_cell+=1;report()
        if time.monotonic()-last_report>=60:report();last_report=time.monotonic()
        time.sleep(5)
    report();save(OUT/'execution.json',dict(finished_at=now(),stop_reason=stop_reason,counts=dict(Counter(r['state'] for r in cells)),formal_eligible=False))

if __name__=='__main__':main()
