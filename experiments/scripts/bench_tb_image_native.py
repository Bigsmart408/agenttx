#!/usr/bin/env python3
"""Recovery protocol validation with agent and official tests in one task container.

The original image workdir and filesystem are retained. No host workspace is
projected into /app and no gold solution is mounted. All attempts are durable;
this new cohort must not be merged with historical host-agent TB results.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]
from experiments.scripts.experiment_provenance import source_manifest
from experiments.scripts.experiment_credentials import private_values, purge_private_codex_auth
from experiments.workloads import terminal_bench_suite as tb

PROTOCOL = 'agenttx.tb-image-native-recovery/v2'
POLICIES = ('clean_recovery', 'crash_no_rollback', 'temporal_checkpoint', 'whole_branch_abort', 'causal')


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def tree_manifest(root):
    root = Path(root)
    return {str(p.relative_to(root)): file_hash(p) for p in sorted(root.rglob('*')) if p.is_file()}


def prime_resolver(harness):
    """Keep Docker's bind-mounted resolver in the transaction's baseline."""
    source = Path('/etc/resolv.conf')
    if not source.read_text().strip():
        raise RuntimeError('task container has no DNS configuration')
    template = Path('/var/tmp/agenttx-docker-resolv.conf')
    shutil.copyfile(source, template)
    pool = harness.tx.pool
    subprocess.run([str(pool.try_bin), '-N', str(pool.session_dir), '--', 'cp',
                    str(template), '/etc/resolv.conf'], cwd=harness.workdir, check=True, timeout=30)
    pool.refresh_summary()
    pool._cached_digests = pool.upperdir_digests()


def compact_recovery_messages(conversation):
    """Match the native SWE view's last six retained action/result pairs.

    The durable conversation stays lossless. Removed calls are omitted only
    from this model view, without applying a second causal closure.
    """
    from agenttx.conversation import ConversationLog
    view = ConversationLog.from_dict(conversation.to_dict())
    calls = [span for turn in view.active_turns() for span in turn.active_calls()]
    if len(calls) > 6:
        for span in calls[:-6]:
            span.status = 'omitted'
        for turn in view.turns:
            turn.refresh_status()
        messages = view.active_messages()
        messages.insert(2, dict(role='user', content=
            'Earlier retained tool observations are omitted from this compact view; '
            'the recovery handoff describes current state.'))
        return messages
    return view.active_messages()


def mount(command, source, destination=None, readonly=True):
    source = Path(source).absolute()
    if not source.exists():
        raise FileNotFoundError(source)
    command.extend(['--mount', f'type=bind,src={source},dst={destination or source}' + (',readonly' if readonly else '')])


def image_identity(image):
    info = json.loads(subprocess.run(['docker', 'image', 'inspect', image],
                     capture_output=True, text=True, check=True).stdout)[0]
    user = info['Config'].get('User') or 'root'
    if user not in {'root', '0', '0:0'}:
        raise RuntimeError(f'image user {user!r} is not yet supported; do not override task user')
    workdir = info['Config'].get('WorkingDir') or ''
    if not workdir.startswith('/') or workdir == '/':
        raise RuntimeError('official image must declare an absolute non-root WorkingDir')
    return dict(id=info['Id'], workdir=workdir, user=user)


def build_command(args, identity, cell, task_file, test_script, tests, local_uv):
    command = ['docker', 'run', '--rm', '--pull', 'never', '--privileged',
               '--cidfile', str(cell/'container.cid'), '--network', 'host',
               '--tmpfs', '/tmp:exec', '--workdir', identity['workdir']]
    # Preserve absolute paths so venv shebangs, editable sources and Python's
    # symlink target resolve without replacing the task interpreter on PATH.
    prefixes = json.loads(subprocess.run([str(args.python), '-c',
        'import json,sys; print(json.dumps([sys.prefix,sys.base_prefix]))'],
        check=True, text=True, capture_output=True).stdout)
    mounts = {ROOT, args.profile_dir.resolve(), *(Path(p).resolve() for p in prefixes)}
    if args.python.is_symlink():
        target = Path(os.readlink(args.python))
        if not target.is_absolute():
            target = args.python.parent/target
        # uv's venv may target a directory alias (3.11 -> 3.11.17).
        # Preserve that spelling as well as the resolved base prefix.
        mounts.add(target.parent.parent.absolute())
    if args.swe_agent_root is not None:
        mounts.add(args.swe_agent_root.resolve())
    for source in sorted(mounts):
        mount(command, source)
    mount(command, cell, '/results', readonly=False)
    mount(command, task_file, '/agenttx-task.json')
    mount(command, test_script, '/opt/agenttx-verifier/run-tests.sh')
    mount(command, tests, '/opt/agenttx-verifier/tests')
    mount(command, local_uv, '/tmp/agenttx-uv')
    mount(command, args.codex_bin, '/usr/local/bin/codex')
    mount(command, args.codex_bin.parent/'codex-code-mode-host', '/usr/local/bin/codex-code-mode-host')
    for tool in ('strace', 'git'):
        mount(command, Path(shutil.which(tool) or f'/usr/bin/{tool}'), f'/usr/bin/{tool}')
    for lib in ('libunwind-ptrace.so.0','libunwind-x86_64.so.8','libunwind.so.8'):
        mount(command, Path('/usr/lib/x86_64-linux-gnu')/lib)
    if args.tiktoken_cache:
        mount(command, args.tiktoken_cache.resolve(), '/opt/agenttx-tiktoken-cache')
        command += ['-e', 'TIKTOKEN_CACHE_DIR=/opt/agenttx-tiktoken-cache']
    env = dict(AGENTTX_MOTIVATION_BWRAP='1', AGENTTX_NO_PROXY='1',
               AGENTTX_WORKER_PYTHON=str(args.python), PYTHONDONTWRITEBYTECODE='1',
               AGENTTX_RESULT_UID=str(os.getuid()), AGENTTX_RESULT_GID=str(os.getgid()),
               PYTHONPATH=f'{ROOT}/src:{ROOT}', SWE_AGENT_LOG_STREAM_LEVEL='ERROR',
               CODEX_CONFIG_FILE=str(args.profile_dir/'config.toml'),
               CODEX_AUTH_FILE=str(args.profile_dir/'auth.json'),
               AGENTTX_SWE_PROFILE=str(args.profile_dir/'sweagent.toml'))
    if args.swe_agent_root:
        env['AGENTTX_SWE_AGENT_ROOT'] = str(args.swe_agent_root)
    env['TRY_BIND_READONLY'] = ':'.join(
        value.split(',dst=',1)[1].split(',',1)[0]
        for value in command if value.startswith('type=bind,') and value.endswith(',readonly'))
    for name, value in env.items():
        command += ['-e', f'{name}={value}']
    command += ['--entrypoint', str(args.python), identity['id'], '-u', str(Path(__file__).resolve()),
                'inside', '--task-file', '/agenttx-task.json', '--output-dir', '/results',
                '--policy', cell.name, '--harness', args.harness, '--model', args.model,
                '--timeout', str(args.timeout), '--verifier-timeout', str(args.verifier_timeout)]
    if args.preflight_only:
        command += ['--preflight-only']
    return command


def run_host(args):
    if args.output_dir.exists():
        raise ValueError('use a new output directory; attempts are immutable')
    args.output_dir.mkdir(parents=True, mode=0o700)
    rows = []
    frozen = source_manifest(ROOT)
    (args.output_dir/'source-manifest.json').write_text(json.dumps(frozen,indent=2))
    registration = dict(protocol=PROTOCOL, protocol_validation_only=True, formal_eligible=False,
        source_sha256=frozen['sha256'], model=args.model, reasoning_effort='max', count_limit=None,
        context_variant='compact', retained_history_call_limit=6,
        wall_timeout=args.timeout, verifier_timeout=args.verifier_timeout, harness=args.harness,
        task=args.task, policies=args.policies, preflight_only=args.preflight_only)
    (args.output_dir/'registration.json').write_text(json.dumps(registration,indent=2))
    try:
        task = tb.load_tasks(args.cache_dir)[args.task]
        source = tb.task_source(args.cache_dir,task)
        if not (source/'tests').is_dir() or not any((source/'tests').rglob('*')):
            raise RuntimeError('official test assets missing; fetch the pinned source assets before model execution')
        image, _ = tb.ensure_official_image(task,args.cache_dir)
        identity = image_identity(image)
        script, local_uv = tb.prepare_official_test_script(source,args.cache_dir)
        local_uv = local_uv or args.uv
        if local_uv is None:
            raise ValueError('provide --uv for the official test bootstrap')
        task_file = args.output_dir/'task.json'
        task_file.write_text(json.dumps(dataclasses.asdict(task),indent=2))
        registration.update(image=identity, tests=tree_manifest(source/'tests'),
            original_test_script_sha256=file_hash(source/'run-tests.sh'),
            effective_test_script_sha256=file_hash(script), uv_sha256=file_hash(local_uv),
            profile_sha256=file_hash(args.profile_dir/('sweagent.toml' if args.harness=='sweagent' else 'config.toml')),
            codex_sha256=file_hash(args.codex_bin), code_mode_host_sha256=file_hash(args.codex_bin.parent/'codex-code-mode-host'))
        (args.output_dir/'registration.json').write_text(json.dumps(registration,indent=2))
        try:
            for policy in args.policies:
                cell = args.output_dir/policy
                cell.mkdir(mode=0o700)
                command = build_command(args,identity,cell,task_file,script,source/'tests',local_uv)
                (cell/'container-command.json').write_text(json.dumps(command,indent=2))
                start=time.perf_counter()
                rc=None; error=None
                try:
                    with (cell/'container.stdout.log').open('w') as out, (cell/'container.stderr.log').open('w') as err:
                        result=subprocess.run(command,stdout=out,stderr=err,
                            timeout=args.timeout+args.verifier_timeout+300,check=False)
                    rc=result.returncode
                except subprocess.TimeoutExpired:
                    error='container wall deadline exceeded'
                finally:
                    cidfile=cell/'container.cid'
                    if cidfile.exists():
                        cid=cidfile.read_text().strip()
                        # Only stop our own container, never enumerate/kill others.
                        subprocess.run(['docker','rm','-f',cid],capture_output=True,check=False,timeout=30)
                path=cell/'result.json'
                row=json.loads(path.read_text()) if path.exists() else dict(completed=False,status='infrastructure_error')
                row.update(container_returncode=rc,host_error=error,container_wall_s=time.perf_counter()-start,
                           policy=policy,image_id=identity['id'])
                if rc != 0 and row.get('completed'):
                    row.update(completed=False,status='infrastructure_error')
                (cell/'host-result.json').write_text(json.dumps(row,indent=2))
                rows.append(row)
                print(json.dumps({k:row.get(k) for k in ('policy','status','completed','error','host_error')}),flush=True)
        finally:
            if script != source/'run-tests.sh': script.unlink(missing_ok=True)
    except Exception as exc:
        rows.append(dict(status='infrastructure_error',completed=False,error=f'{type(exc).__name__}: {exc}'))
        (args.output_dir/'host-error.txt').write_text(traceback.format_exc())
    (args.output_dir/'summary.json').write_text(json.dumps(rows,indent=2))
    return 0 if rows and all(r['completed'] for r in rows) else 1


def run_inside(args):
    from agenttx.harness import CodingAgentHarness
    from agenttx.policy import CommitPolicy
    from experiments.scripts.bench_official_tasks import _create_workload_harness, _apply_blind_policy
    from experiments.workloads.recovery_inject import all_midcrash_docs, document_text_valid
    output=args.output_dir; workspace=Path.cwd()
    task=tb.TBTask(**json.loads(args.task_file.read_text()))
    row=dict(protocol=PROTOCOL,protocol_validation_only=True,formal_eligible=False,
             task=task.task_id,policy=args.policy,harness=args.harness,
             model=args.model,reasoning_effort='max',count_limit=None,
             context_variant='compact',retained_history_call_limit=6,
             workspace=str(workspace),agent_runtime='official_task_container',
             verifier_runtime='same_official_task_container',timings={})
    started=time.perf_counter(); h=None
    try:
        for tool in ('git','strace'):
            subprocess.run([tool,'--version'],check=True,capture_output=True)
        task_python=subprocess.run(['python3','-c','import sys;print(sys.executable);print(sys.version)'],
                                   capture_output=True,text=True,check=True).stdout
        row['task_python']=task_python
        row['worker_python']=sys.executable
        h=CodingAgentHarness(workspace,session_dir=output/'session',trace_backend='strace',
            policy=CommitPolicy(workspace,allow_external_writes=True,ephemeral_roots=(Path('/root'),Path('/tmp'))))
        tx=h.tx
        prime_resolver(h)
        gate=tx.run_tool('runtime-preflight',['bash','-c','printf runtime-ok'],trace_reads=True,timeout_s=30)
        if gate.returncode or gate.stdout.strip()!='runtime-ok':
            raise RuntimeError(f'task image transaction preflight failed: {gate.stderr}')
        # Preflight is infrastructure, not part of the task trajectory.
        tx.rollback(gate.step_id)
        if args.preflight_only:
            row.update(completed=True,status='preflight_passed',official_success=None)
        else:
            docs=all_midcrash_docs(task.docs())
            doc_contract='\n'.join(f'{d.path}: title "# {d.prefix.title()}" then {d.lines} lines in the exact form '+
                f'"{d.prefix.upper()}-NNN: verified {task.task_id} work item NNN records a repository decision." '+
                '(NNN counts from 001).' for d in docs)
            task_text=(task.instruction+'\n\nAlso preserve or recreate the session notes required by the original task:\n'+
                doc_contract+'\nRemove recovery_build/derived.txt if present. Work in the original image working directory. '
                'Use its installed task runtimes. Do not change official tests.')
            tx.conversation.seed('You are a coding agent. Complete the original task using shell tools.',task_text)
            injected={}; targets=[]; localization={}
            if args.policy!='clean_recovery':
                agent=type('Agent',(),{'harness':h})()
                injected=tb.inject_task_trajectory(agent,task,sys.executable)
                if not injected['tests_failed']:
                    raise RuntimeError('injected fault did not fail the crash contract')
                precrash=tx.conversation.to_dict()
                stamp=time.perf_counter()
                if args.policy=='causal':
                    # Existing failed path-blind marker contract supplies all
                    # evidence; root/closure fields are never sent to localizer.
                    failed=next(r for r in tx.history if r.step_id==injected['test_run_step'])
                    # Run a fresh verifier record using the original command.
                    command=failed.argv
                    failed_record=tx.run_verifier(command,trace_reads=True,timeout_s=60)
                    recovery=tx.plan_recovery(failed_record,max_candidates=None,max_probes=24,
                        allow_fallback=False,verifier_trace_reads=True,verifier_timeout_s=60)
                    localization=dataclasses.asdict(recovery)
                    if not recovery.verified:
                        raise RuntimeError('causal recovery could not validate its localization')
                    targets=tx.rollback_causal_many(recovery.root_step_ids)
                else:
                    targets,localization=_apply_blind_policy(agent,injected,args.policy)
                row['timings']['policy_wall_s']=time.perf_counter()-stamp
            else:
                precrash=tx.conversation.to_dict()
            row.update(rollback_ids=targets,localization=localization,injected=injected)
            (output/'precrash-conversation.json').write_text(json.dumps(precrash,indent=2))
            (output/'recovered-conversation.json').write_text(json.dumps(tx.conversation.to_dict(),indent=2))
            tx.dump_ledger(output/'recovered-ledger.json')
            active={s.step_id for s in tx.ledger.active_uncommitted()}
            original=set(injected.get('independent_steps',[]))|set(injected.get('oracle_invalid_steps',[]))
            row['conversation_matches_workspace']=set(tx.conversation.active_step_ids()).intersection(original)==active.intersection(original)
            if not row['conversation_matches_workspace']: raise RuntimeError('recovery context mismatch')
            prompt=task_text+'\nResume the original task after an interruption from the current workspace and active history.'
            (output/'prompt.txt').write_text(prompt)
            adapter=_create_workload_harness(args.harness,harness_root=None,model=args.model,harness_command=None,
                harness_timeout_s=args.timeout,output_dir=output,max_turns=None,reasoning_effort='max')
            stamp=time.perf_counter()
            if args.harness=='sweagent':
                # Clean tasks have no tool trajectory to seed or reconstruct.
                context = (dict(original_task=task_text,precrash_conversation=precrash,context_variant='compact')
                           if injected else None)
                result=adapter.run_in_transaction(h,prompt,native_context=context)
            else:
                result=adapter.run_in_transaction(h,prompt+'\n'+json.dumps(compact_recovery_messages(tx.conversation)))
            row['timings']['model_wall_s']=time.perf_counter()-stamp
            (output/'model-result.json').write_text(json.dumps(dataclasses.asdict(result),indent=2))
            row.update(model_returncode=result.returncode,prompt_tokens=result.prompt_tokens,
                       completion_tokens=result.completion_tokens,total_tokens=result.total_tokens,
                       usage_source=result.usage_source,tool_calls=result.tool_calls)
            tx.dump_ledger(output/'final-ledger.json')
            # Commit any safely committable partial model work so official tests
            # can grade it even when the model exits unsuccessfully.
            tx.commit()
            h.close(destroy=False); h=None
            row['documents_valid']=all(document_text_valid((workspace/d.path).read_text(),d.prefix,d.lines)
                                      if (workspace/d.path).is_file() else False for d in docs)
            row['derived_removed']=not (workspace/'recovery_build/derived.txt').exists()
            row['model_finished']=result.returncode==0
        if not args.preflight_only:
            tests=Path('/tests'); tests.mkdir(exist_ok=True)
            shutil.copytree('/opt/agenttx-verifier/tests',tests,dirs_exist_ok=True)
            verifier_env={**os.environ,'TEST_DIR':'/tests'}
            stamp=time.perf_counter()
            try:
                with (output/'verifier.stdout.log').open('w') as out, (output/'verifier.stderr.log').open('w') as err:
                    verdict=subprocess.run(['bash','/opt/agenttx-verifier/run-tests.sh'],cwd=workspace,
                        env=verifier_env,stdout=out,stderr=err,timeout=args.verifier_timeout,check=False)
                rc=verdict.returncode
            except subprocess.TimeoutExpired: rc=124
            row['timings']['verification_wall_s']=time.perf_counter()-stamp
            row['official_tests_rc']=rc
            row['official_success']=rc==0
            row['completed']=bool(row['model_finished'] and row['documents_valid'] and row['derived_removed'] and rc==0)
            row['status']=('passed' if row['completed'] else 'harness_error' if result.returncode==127
                           else 'verifier_timeout' if rc==124 else 'task_or_model_failed')
    except Exception as exc:
        row.update(completed=False,status='infrastructure_error',error=f'{type(exc).__name__}: {exc}')
        (output/'error.txt').write_text(traceback.format_exc())
    finally:
        if h is not None: h.close(destroy=False)
        removed = purge_private_codex_auth(output, private_values())
        if removed:
            (output/'private-auth-cleanup.json').write_text(json.dumps(removed,indent=2))
        row['timings']['full_wall_s']=time.perf_counter()-started
        (output/'result.json').write_text(json.dumps(row,indent=2))
        # Host owns the evidence; do not chown the image workspace or its inputs.
        uid=int(os.environ.get('AGENTTX_RESULT_UID','0')); gid=int(os.environ.get('AGENTTX_RESULT_GID','0'))
        for p in [output,*output.rglob('*')]:
            if not p.is_symlink():
                try: os.chown(p,uid,gid)
                except OSError: pass
    return 0 if row['completed'] else 1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    host=sub.add_parser('host'); inside=sub.add_parser('inside')
    for p in (host,inside):
        p.add_argument('--output-dir',type=Path,required=True)
        p.add_argument('--harness',choices=('codex','sweagent'),required=True)
        p.add_argument('--model',default='gpt-5.6-luna')
        p.add_argument('--timeout',type=float,default=240)
        p.add_argument('--verifier-timeout',type=float,default=600)
        p.add_argument('--preflight-only',action='store_true')
    host.add_argument('--task',required=True)
    host.add_argument('--policies',nargs='+',choices=POLICIES,default=list(POLICIES))
    host.add_argument('--cache-dir',type=Path,required=True)
    host.add_argument('--python',type=Path,default=Path(sys.executable))
    host.add_argument('--profile-dir',type=Path,required=True)
    host.add_argument('--swe-agent-root',type=Path)
    host.add_argument('--codex-bin',type=Path,required=True)
    host.add_argument('--uv',type=Path)
    host.add_argument('--tiktoken-cache',type=Path)
    inside.add_argument('--task-file',type=Path,required=True)
    inside.add_argument('--policy',choices=POLICIES,required=True)
    args=parser.parse_args()
    if args.command=='host':
        args.output_dir=args.output_dir.resolve()
        args.python=args.python.absolute(); args.profile_dir=args.profile_dir.resolve()
        args.codex_bin=args.codex_bin.absolute()
        if len(args.policies)!=len(set(args.policies)): parser.error('duplicate policies')
        if args.harness=='sweagent' and args.swe_agent_root is None: parser.error('SWE-agent needs --swe-agent-root')
        return run_host(args)
    if os.environ.get('AGENTTX_MOTIVATION_BWRAP')!='1': parser.error('inside requires a disposable task container')
    return run_inside(args)


if __name__=='__main__':
    raise SystemExit(main())
