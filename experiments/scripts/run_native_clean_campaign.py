#!/usr/bin/env python3
"""Record autonomous, fault-free SWE-Bench/TB runs with both native agents.

Reuse the existing task-image, service, and official-verifier setup. The model
receives the original task, with no synthetic workload, crash, or seeded steps.
Keep native dialogues, per-tool effects, and verifier outcomes independently.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import dataclasses
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import shlex
import shutil
import subprocess
import sys
import time
import traceback
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
DATA = Path('/mnt/data/agenttx-runtime')
PYTHON = Path('/home/bfq/.venvs/agenttx/bin/python')
PROFILE = Path('/home/bfq/.config/agenttx/experiment-20261017')
SWE = Path('/home/bfq/agenttx-workspace/agenttx-deps/SWE-agent-3ea751c087f32b16e039a2233dd6eefecef325d5')
CODEX = Path('/home/bfq/.local/lib/node_modules/@openai/codex/node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex')
TOOLS = DATA / 'validation/tb-validation-tools-20261018'
PROTOCOL = 'agenttx.autonomous-native-clean/v1'


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def digest(path):
    with Path(path).open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def inside(args):
    from experiments.scripts import bench_tb_image_native as runtime
    from experiments.scripts.bench_official_tasks import _create_workload_harness
    from experiments.scripts.experiment_credentials import private_values, redact_credentials, task_user_codex_profile
    from agenttx.harness import CodingAgentHarness
    from agenttx.policy import CommitPolicy

    output, workspace = args.output_dir, Path.cwd()
    task_data = json.loads(args.task_file.read_text())
    task_id = task_data.get('task_id', task_data.get('task'))
    instruction = task_data.get('instruction', '')
    row = dict(protocol=PROTOCOL, task=task_id, suite=args.suite, harness=args.harness,
               fault_injected=False, precrash_progress_seeded=False, model=args.model,
               reasoning_effort='max', model_session_wall_s=args.timeout,
               completed=False, status='infrastructure_error', started_at=now())
    h = None
    stack = ExitStack()
    start = time.monotonic()
    try:
        runtime.configure_task_download_timeout()
        runtime.configure_task_hf_cache()
        row['runtime_tools'] = runtime.prepare_runtime_tools(output)
        from experiments.scripts.sweagent_native_workload import _initialize_workspace_repo
        if args.suite == 'swe':
            # Official image setup commits may differ from the task's base.
            subprocess.run(['git', 'reset', '--hard', task_data['base_commit']],
                           cwd=workspace, check=True, capture_output=True)
            row['base_commit'] = subprocess.check_output(['git', 'rev-parse', 'HEAD'],
                                                        text=True).strip()
            assert row['base_commit'] == task_data['base_commit']
        task_user = os.environ.get('AGENTTX_TB_TASK_USER') or None
        row['original_task_user'] = task_user or 'root'
        if task_user:
            stack.enter_context(task_user_codex_profile(task_user))
        _initialize_workspace_repo(workspace, task_user=task_user)
        prompt = instruction + '\n\nWork in the original task directory using its installed runtimes.'
        (output / 'prompt.txt').write_text(prompt)
        if args.preflight_only:
            # Imports and actual syscall tracing are checked without a model call.
            subprocess.run(['strace', '-f', '-e', 'trace=%file', '-o', str(output/'preflight.strace'),
                            'git', 'status', '--porcelain'], check=True, capture_output=True)
            if args.harness == 'sweagent':
                _create_workload_harness('sweagent', harness_root=SWE, model=args.model,
                    harness_command=None, harness_timeout_s=args.timeout,
                    output_dir=output/'model', max_turns=None, reasoning_effort='max')
            else:
                subprocess.run(['codex', '--version'], check=True, capture_output=True)
            row.update(completed=True, status='preflight_passed', model_calls=0)
            return 0
        if args.harness == 'sweagent':
            from experiments.scripts.micro_model_usage import model_usage
            h = CodingAgentHarness(workspace, session_dir=output/'session', trace_backend='strace',
                tool_user=task_user, policy=CommitPolicy(workspace, allow_external_writes=True,
                ephemeral_roots=(Path('/root'), Path('/tmp'))))
            runtime.prime_resolver(h)
            adapter = _create_workload_harness('sweagent', harness_root=SWE, model=args.model,
                harness_command=None, harness_timeout_s=args.timeout,
                output_dir=output/'model', max_turns=None, reasoning_effort='max')
            with model_usage(output/'api-usage.json', None) as usage:
                result = adapter.run_in_transaction(h, prompt)
            metadata = result.metadata
            record = dict(schema='agenttx.clean_native_trace/v2', task=task_id,
                suite='swe_bench_lite' if args.suite=='swe' else 'terminal_bench',
                source_system='SWE-agent native / Luna max / official Docker image',
                workspace_root=str(workspace), fault_injected=False,
                precrash_progress_seeded=False, source_success=None,
                source_completed=result.returncode==0, model_finished=result.returncode==0,
                termination_reason=metadata.get('sweagent_exit_status'),
                step_limit_reached=False, config={**adapter.profile.public_dict(),
                    'sweagent_config_hash':adapter.config_hash},
                ledger=h.tx.ledger.to_dict(), conversation=h.tx.conversation.to_dict(),
                result=dict(summary=metadata.get('sweagent_exit_status'),
                    tool_calls=result.tool_calls, model_calls=metadata.get('sweagent_model_calls'),
                    prompt_tokens=usage['prompt_tokens'], completion_tokens=usage['completion_tokens']))
            save(output/'model-result.json', dataclasses.asdict(result))
            h.tx.dump_ledger(output/'final-ledger.json')
            save(output/'final-conversation.json', h.tx.conversation.to_dict())
            model_rc = result.returncode
            row['api_usage'] = usage
        else:
            destination = output/'codex'
            destination.mkdir()
            run_options = {}
            if task_user:
                account = pwd.getpwnam(task_user)
                output.chmod(0o755)
                os.chown(destination, account.pw_uid, account.pw_gid)
                run_options.update(user=account.pw_uid, group=account.pw_gid, extra_groups=[],
                    env=dict(os.environ, HOME=account.pw_dir, USER=account.pw_name, LOGNAME=account.pw_name))
            command = [sys.executable, str(ROOT/'experiments/scripts/capture_clean_codex_in_image.py'),
                '--suite', args.suite, '--task', task_id, '--workspace', str(workspace),
                '--prompt-file', str(output/'prompt.txt'), '--output-dir', str(destination),
                '--model', args.model, '--reasoning-effort', 'max', '--max-steps', '0',
                '--max-model-calls', '0', '--timeout-s', str(args.timeout)]
            subprocess.run(command, check=True, **run_options)
            record = json.loads((destination/'native_traces'/f'codex__{args.suite}__{task_id}.json').read_text())
            model_rc = 124 if record.get('model_timed_out') else (0 if record['model_finished'] else 1)
            row['total_tokens'] = sum(record['result'].get(k, 0) or 0 for k in ('prompt_tokens','completion_tokens'))
        # Save the solver trace before grading. Grading calls never enter it.
        trace = output/'native-trace.json'
        save(trace, redact_credentials(record, private_values()))
        row.update(model_returncode=model_rc, source_completed=record['source_completed'],
                   capture_valid=record.get('capture_valid', True),
                   trace_steps=len(record['ledger']['steps']), trace_path=str(trace))
        verify_command = ['bash', '/opt/agenttx-verifier/verify.sh']
        if h:
            if args.suite == 'tb':
                runtime.restore_official_test_assets(h.tx, preserve_directory=bool(task_user))
            verifier_rc = runtime.verify_transaction_view(h.tx, verify_command, output, args.verifier_timeout)
        else:
            if args.suite == 'tb':
                subprocess.run(['bash', '-c', 'mkdir -p /tests; cp -a /opt/agenttx-verifier/tests/. /tests/'], check=True)
            with (output/'verifier.stdout.log').open('w') as stdout, (output/'verifier.stderr.log').open('w') as stderr:
                try:
                    verifier_rc = subprocess.run(verify_command, stdout=stdout, stderr=stderr,
                                                timeout=args.verifier_timeout).returncode
                except subprocess.TimeoutExpired:
                    verifier_rc = 124
        record['source_success'] = None if verifier_rc in (124,127) else verifier_rc==0
        record['verifier'] = dict(runtime='same_official_task_container', tests_rc=verifier_rc,
                                  tests_ok=verifier_rc==0, infrastructure_error=verifier_rc in (124,127))
        save(trace, redact_credentials(record, private_values()))
        row.update(completed=True, status='passed' if verifier_rc==0 else 'task_or_model_failed',
                   official_tests_rc=verifier_rc, official_success=verifier_rc==0)
    except Exception as exc:
        row['error'] = f'{type(exc).__name__}: {exc}'
        (output/'error.txt').write_text(traceback.format_exc())
    finally:
        if h:
            h.close(destroy=False)
        stack.close()
        row.update(finished_at=now(), wall_s=time.monotonic()-start)
        save(output/'result.json', row)
        uid, gid = int(os.environ.get('AGENTTX_RESULT_UID', '0')), int(os.environ.get('AGENTTX_RESULT_GID','0'))
        for path in [output, *output.rglob('*')]:
            if not path.is_symlink():
                try:
                    os.chown(path, uid, gid)
                except OSError:
                    pass
    return 0 if row['completed'] else 1


def host(args):
    from experiments.scripts import bench_tb_image_native as runtime
    from experiments.scripts.prepare_tb_portable_git import verified_prerequisite
    original_build = runtime.build_command
    def build(*items):
        command = original_build(*items)
        index = command.index(str(Path(runtime.__file__).resolve()))
        command[index] = str(Path(__file__).resolve())
        command += ['--suite', args.suite]
        return command
    runtime.build_command = build
    args.python, args.profile_dir, args.swe_agent_root = PYTHON, PROFILE, SWE
    args.codex_bin, args.uv, args.tiktoken_cache = CODEX, TOOLS/'bin/uv', DATA/'tiktoken-cache'
    args.policies, args.task_user_adapter = ['clean_recovery'], True
    args.static_strace = TOOLS/'static-strace/strace-6.13+ds/src/strace'
    args.fault_seed, args.verifier_evidence = 'unused-clean', None
    if args.suite == 'tb':
        rc = runtime.run_host(args)
        registration = json.loads((args.output_dir/'registration.json').read_text())
        registration.update(protocol=PROTOCOL, fault_injected=False, precrash_progress_seeded=False,
                            completion_contract='original_task_only', retained_history_call_limit=None)
        save(args.output_dir/'registration.json', registration)
        return rc
    from experiments.workloads import swe_bench_suite as swe
    from experiments.scripts.tb_task_services import TaskServices
    args.output_dir.mkdir(parents=True, exist_ok=False)
    instance = {x['instance_id']:x for x in swe.fetch_all_instances(args.cache_dir)}[args.task]
    image = swe.swe_eval_image(args.task)
    identity = runtime.image_identity(image)
    task_file = args.output_dir/'task.json'
    save(task_file, dict(task=args.task, instruction=instance['problem_statement'], base_commit=instance['base_commit']))
    tests = args.output_dir/'grading-assets'
    tests.mkdir()
    (tests/'test.patch').write_text(instance.get('test_patch') or '')
    task = swe.load_tasks(args.cache_dir)[args.task]
    grade = '#!/bin/bash\nset -e\ncd /testbed\n'
    if instance.get('test_patch'):
        grade += 'git apply /opt/agenttx-verifier/tests/test.patch\n'
    grade += swe.test_command(task, instance, swe.DOCKER_TESTBED_PYTHON) + '\n'
    args.verifier_entrypoint = args.output_dir/'official-verifier.sh'
    args.verifier_entrypoint.write_text(grade)
    args.verifier_mounts, args.verifier_setup, args.worker_compatibility = [], None, None
    args.portable_git, args.portable_git_wrapper, git_proof = verified_prerequisite()
    cell = args.output_dir/'clean_recovery'
    cell.mkdir()
    source = args.output_dir/'network-source'
    source.mkdir()
    services = TaskServices(source, args.task, cell, identity['id']).start()
    save(args.output_dir/'registration.json', dict(protocol=PROTOCOL, suite='swe', task=args.task,
        harness=args.harness, image=identity, base_commit=instance['base_commit'],
        task_prompt_sha256=hashlib.sha256(instance['problem_statement'].encode()).hexdigest(),
        fault_injected=False, precrash_progress_seeded=False, model=args.model, reasoning_effort='max'))
    try:
        command = services.apply(build(args, identity, cell, task_file, args.verifier_entrypoint, tests, args.uv))
        save(cell/'container-command.json', command)
        with (cell/'container.stdout.log').open('w') as out, (cell/'container.stderr.log').open('w') as err:
            rc = subprocess.run(command, stdout=out, stderr=err,
                                timeout=args.timeout+args.verifier_timeout+900).returncode
        return rc
    finally:
        cidfile = cell/'container.cid'
        if cidfile.exists():
            subprocess.run(['docker','rm','-f',cidfile.read_text().strip()], capture_output=True, timeout=30)
        services.close()


def prepare(args):
    from experiments.scripts.experiment_provenance import source_manifest
    from experiments.workloads import swe_bench_suite as swe, terminal_bench_suite as tb
    cohort = args.cohort.resolve()
    cohort.mkdir(mode=0o700, parents=True, exist_ok=False)
    source = Path('/home/bfq/agenttx-workspace/agenttx-executions')/cohort.name/'project'
    source.mkdir(parents=True, exist_ok=False)
    manifest = source_manifest(ROOT)
    for entry in manifest['files']:
        target = source/entry['path']
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/entry['path'], target)
    assert source_manifest(source)['sha256']==manifest['sha256']
    save(cohort/'source-manifest.json', manifest)
    tasks = dict(swe=sorted(swe.load_tasks(DATA/'cache')), tb=sorted(tb.load_tasks(DATA/'cache')))
    assert len(tasks['swe'])==300 and len(tasks['tb'])==241
    # Fixed early tasks exercise each combination; they remain in the full cohort.
    early = dict(swe='astropy__astropy-12907', tb='hello-world')
    for suite in tasks:
        if early[suite] in tasks[suite]:
            tasks[suite].remove(early[suite]); tasks[suite].insert(0,early[suite])
    cells=[]
    for i in range(300):
        for suite in ('swe','tb'):
            if i<len(tasks[suite]):
                for agent in ('sweagent','codex'):
                    cells.append(dict(suite=suite,task=tasks[suite][i],harness=agent,state='pending'))
    save(cohort/'cells.json', cells)
    save(cohort/'registration.json', dict(protocol=PROTOCOL, registered_at=now(),
        frozen_source=str(source),source_sha256=manifest['sha256'], expected_cells=len(cells),
        workers=args.workers, model='gpt-5.6-luna', reasoning_effort='max',
        model_wall_s=3600, verifier_wall_s=1800, tool_step_limit=None, model_call_limit=None,
        fault_injected=False,precrash_progress_seeded=False, ordinary_retries=0,
        retention='Keep native model dialogues, tool effects, conversation, trace exports, outcomes and provenance; no detailed-ledger cleanup.',
        profile_sha256={name:digest(PROFILE/name) for name in ('config.toml','sweagent.toml')},
        counts={f'{s}/{h}':len(tasks[s]) for s in tasks for h in ('sweagent','codex')}))
    print(json.dumps(dict(cohort=str(cohort),source=str(source),cells=len(cells))))


def campaign(args):
    cohort=args.cohort.resolve()
    lock=(cohort/'coordinator.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX|fcntl.LOCK_NB)
    registration=json.loads((cohort/'registration.json').read_text())
    cells=json.loads((cohort/'cells.json').read_text())
    source=Path(registration['frozen_source'])
    active=[]
    stop_reason=None
    def report():
        save(cohort/'cells.json',cells)
        save(cohort/'progress.json',dict(updated_at=now(),counts=dict(Counter(c['state'] for c in cells)),
             active=[{k:c[k] for k in ('suite','task','harness','pid')} for c,_,_ in active],
             stop_reason=stop_reason,workers=registration['workers']))
    for c in cells:
        if c['state']=='running':
            raise RuntimeError('Interrupted running cells require explicit reconciliation; no automatic retry')
    while True:
        for c,proc,log in list(active):
            rc=proc.poll()
            if rc is None:continue
            log.close();active.remove((c,proc,log))
            folder=Path(c['output'])/'clean_recovery'
            path=folder/'result.json'
            r=json.loads(path.read_text()) if path.exists() else {}
            trace=folder/'native-trace.json'
            c.update(state='finished' if r.get('completed') else 'infrastructure_error',
                     returncode=rc,finished_at=now(),trace_path=str(trace) if trace.exists() else None,
                     source_completed=r.get('source_completed'),official_success=r.get('official_success'),
                     capture_valid=r.get('capture_valid'),trace_steps=r.get('trace_steps'))
            if c['state']=='infrastructure_error':
                stop_reason='Inspect infrastructure failure before dispatching more model calls'
        if shutil.disk_usage(cohort).free<150*1024**3 or shutil.disk_usage('/mnt/data').free<150*1024**3:
            stop_reason='Insufficient disk space'
        while not stop_reason and len(active)<registration['workers']:
            # Audit all four initial native runs before opening the full queue.
            if any(c['state']=='running' for c in cells[:4]) and all(c['state']!='pending' for c in cells[:4]):
                break
            if all(c['state']=='finished' for c in cells[:4]):
                if not all(c.get('trace_steps',0)>0 and c.get('capture_valid') for c in cells[:4]):
                    stop_reason='Initial native traces failed capture validation'
                    break
            c=next((c for c in cells if c['state']=='pending'),None)
            if c is None:break
            folder=cohort/'cells'/c['suite']/c['harness']/c['task']
            folder.parent.mkdir(parents=True,exist_ok=True)
            log_path=folder.parent/(c['task']+'.worker.log')
            log=log_path.open('w')
            command=[str(PYTHON),'-u',str(source/'experiments/scripts/run_native_clean_campaign.py'),'host',
                '--suite',c['suite'],'--harness',c['harness'],'--task',c['task'],
                '--output-dir',str(folder)]
            proc=subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                env=dict(os.environ,DOCKER_HOST='unix:///run/agenttx-docker/docker.sock',
                         AGENTTX_NO_PROXY='1',AGENTTX_DOMESTIC_ONLY='1',AGENTTX_DOWNLOAD_PROXY_RETRY='1'),
                start_new_session=True)
            c.update(state='running',pid=proc.pid,started_at=now(),output=str(folder))
            active.append((c,proc,log))
        report()
        if not active and (stop_reason or all(c['state']!='pending' for c in cells)):break
        time.sleep(5)
    return 1 if stop_reason else 0


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    subs=parser.add_subparsers(dest='command',required=True)
    for name in ('prepare','run'):
        p=subs.add_parser(name);p.add_argument('--cohort',type=Path,required=True)
        p.add_argument('--workers',type=int,default=4)
    for name in ('host','inside'):
        p=subs.add_parser(name)
        p.add_argument('--suite',choices=('swe','tb'),required=True)
        p.add_argument('--harness',choices=('sweagent','codex'),required=True)
        p.add_argument('--output-dir',type=Path,required=True)
        p.add_argument('--model',default='gpt-5.6-luna')
        p.add_argument('--timeout',type=float,default=3600)
        p.add_argument('--verifier-timeout',type=float,default=1800)
        p.add_argument('--preflight-only',action='store_true')
        if name=='host':
            p.add_argument('--task',required=True)
            p.add_argument('--cache-dir',type=Path,default=DATA/'cache')
        else:
            p.add_argument('--task-file',type=Path,required=True)
            p.add_argument('--policy',choices=('clean_recovery',),required=True)
            p.add_argument('--fault-seed')
            p.add_argument('--prepared-verifier',action='store_true')
    args=parser.parse_args()
    if args.command=='prepare':return prepare(args)
    if args.command=='run':return campaign(args)
    if args.command=='host':return host(args)
    if os.environ.get('AGENTTX_MOTIVATION_BWRAP')!='1':parser.error('inside requires a disposable task image')
    return inside(args)


if __name__=='__main__':
    raise SystemExit(main())
