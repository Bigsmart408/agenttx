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
import re
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

PROTOCOL = 'agenttx.tb-image-native-recovery/v6'
COMPLETION_CONTRACT = 'explicit-derived-cleanup/v1'
SUCCESS_POLICY = 'accept-verified-model-timeout/v1'
MODEL_TIMEOUT_S = 3600
VERIFIER_TIMEOUT_S = 1800
COORDINATOR_GIT = Path('/usr/local/bin/git')
POLICIES = ('clean_recovery', 'crash_no_rollback', 'temporal_checkpoint', 'whole_branch_abort', 'causal', 'ideal_oracle')
CONTEXTS = {p: ('compact' if p in ('causal', 'ideal_oracle', 'clean_recovery') else 'full') for p in POLICIES}


def recovery_messages(conversation, policy):
    return compact_recovery_messages(conversation) if CONTEXTS[policy] == 'compact' else conversation.active_messages()


def outcome_success(model_rc, verifier_rc, contracts_valid):
    # A session deadline is not a correctness failure when its final workspace
    # passes both the official verifier and the disclosed recovery contract.
    return bool(model_rc in (0, 124) and verifier_rc == 0 and contracts_valid)


def result_success(row):
    """Apply the current outcome rule without rewriting immutable old results."""
    return bool(row.get('completed') and outcome_success(
        row.get('model_returncode'), row.get('official_tests_rc'),
        row.get('documents_valid') and row.get('derived_removed')))


def completion_prompt(task_text):
    """Disclose the same scored cleanup obligation to every treatment arm.

    This fixed synthetic output is part of the public completion contract,
    not a localized fault source or an oracle step. Cleanup is performed by
    the agent, so its tool calls and API cost remain in the observation.
    """
    return task_text + (
        '\n\nRecovery completion requirements (identical for all policies):\n'
        'Before finishing, ensure `recovery_build/derived.txt` is absent. '
        'This reserved file is an invalid synthetic crash-derived artifact, '
        'not an official task output; remove it if it exists. '
        'Preserve the protected independent recovery notes. '
        'A passing official test alone does not complete recovery while this '
        'artifact remains. Do not change official tests.\n'
        'Work in the original image working directory using its installed runtimes.'
    )


def preflight_completion_contract(harness):
    """Check the scored state in the real private view, without a model call."""
    tx=harness.tx
    artifact=Path(harness.workdir)/'recovery_build/derived.txt'
    if tx.path_exists(artifact):
        raise RuntimeError('completion preflight requires a clean disposable task image')
    seeded=tx.run_tool('completion-preflight-seed',['bash','-c',
        'mkdir -p recovery_build recovery_notes && '
        'printf invalid > recovery_build/derived.txt && '
        'printf protected > recovery_notes/agenttx_completion_preflight.md'],timeout_s=30)
    if seeded.returncode or not tx.path_exists(artifact):
        raise RuntimeError('completion preflight could not observe injected artifact')
    if outcome_success(124,0,not tx.path_exists(artifact)):
        raise RuntimeError('completion preflight accepted unclean state')
    cleaned=tx.run_tool('completion-preflight-agent-cleanup',['bash','-c',
        'rm -f recovery_build/derived.txt && '
        'test "$(cat recovery_notes/agenttx_completion_preflight.md)" = protected'],timeout_s=30)
    absent=not tx.path_exists(artifact)
    if cleaned.returncode or not absent or not outcome_success(124,0,absent):
        raise RuntimeError('completion preflight cleanup/preservation check failed')
    return dict(unclean_rejected=True,agent_cleanup_observed=True,notes_preserved=True,
                verified_timeout_accepted=True,model_calls=0)


def outcome_status(model_rc, verifier_rc, contracts_valid):
    # A failed task is a completed observation, not a broken experiment worker.
    if model_rc == 127:
        return False, 'harness_error'
    if verifier_rc == 124:
        return True, 'verifier_timeout'
    if outcome_success(model_rc, verifier_rc, contracts_valid):
        return True, 'passed_after_model_timeout' if model_rc == 124 else 'passed'
    return True, 'task_or_model_failed'


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


def image_identity(image, allow_task_user=False):
    info = json.loads(subprocess.run(['docker', 'image', 'inspect', image],
                     capture_output=True, text=True, check=True).stdout)[0]
    user = info['Config'].get('User') or 'root'
    if user not in {'root', '0', '0:0'} and not (allow_task_user and re.fullmatch(r'[a-z_][a-z0-9_-]*',user)):
        raise RuntimeError(f'image user {user!r} is not yet supported; do not override task user')
    workdir = info['Config'].get('WorkingDir') or ''
    if not workdir.startswith('/') or workdir == '/':
        raise RuntimeError('official image must declare an absolute non-root WorkingDir')
    return dict(id=info['Id'], workdir=workdir, user=user)


def official_tests_directory(source, output):
    tests = source/'tests'
    if tests.is_dir() and any(tests.rglob('*')):
        return tests, 'tests_directory'
    script = source/'run-tests.sh'
    text = script.read_text() if script.exists() else ''
    if ('cat > /app/test_patch.diff' in text
            and 'patch --fuzz=5 -p1 -i /app/test_patch.diff' in text
            and 'pytest ' in text):
        tests = output/'embedded-test-assets'
        tests.mkdir()
        return tests, 'inline_patch_in_official_script'
    raise RuntimeError('official test assets missing; fetch the pinned source assets before model execution')


def build_command(args, identity, cell, task_file, test_script, tests, local_uv):
    command = ['docker', 'run', '--rm', '--pull', 'never', '--privileged',
               '--cidfile', str(cell/'container.cid'), '--network', 'host',
               '--tmpfs', '/tmp:exec', '--workdir', identity['workdir']]
    task_user=identity['user'] if identity['user'] not in {'root','0','0:0'} else None
    if task_user:command += ['--user','0']  # namespace coordinator only; pool drops every task command
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
    if getattr(args,'worker_compatibility',None):
        native,target,_=args.worker_compatibility
        mount(command,native,str(target))
    mount(command, cell, '/results', readonly=False)
    mount(command, task_file, '/agenttx-task.json')
    mount(command, test_script, '/opt/agenttx-verifier/run-tests.sh')
    mount(command, tests, '/opt/agenttx-verifier/tests')
    for source, destination in getattr(args, 'verifier_mounts', []):
        mount(command, source, destination)
    if getattr(args, 'verifier_setup', None):
        mount(command, args.verifier_setup, '/opt/agenttx-verifier/setup.sh')
    mount(command, local_uv, '/tmp/agenttx-uv')
    portable_git=getattr(args,'portable_git',None)
    if portable_git:
        mount(command,portable_git/'root','/opt/agenttx-portable-git')
        mount(command,args.portable_git_wrapper,'/opt/agenttx-prerequisites/git')
    mount(command, args.codex_bin, '/usr/local/bin/codex')
    mount(command, args.codex_bin.parent/'codex-code-mode-host', '/usr/local/bin/codex-code-mode-host')
    # Host executables may require a newer glibc than the task image. Keep
    # the image's git and use the separately verified portable tracer bundle.
    portable = Path(os.environ.get('AGENTTX_TB_PORTABLE_STRACE',
        '/mnt/data/agenttx-runtime/validation/tb-validation-tools-20261018/portable-strace/root'))
    if getattr(args,'static_strace',None):
        mount(command,args.static_strace,'/usr/bin/strace')
    else:
        mount(command, portable/'usr/bin/strace', '/usr/bin/strace')
        for lib in ('libunwind-ptrace.so.0','libunwind-x86_64.so.8','libunwind.so.8'):
            mount(command, (portable/'usr/lib/x86_64-linux-gnu'/lib).resolve(),
                  '/usr/lib/x86_64-linux-gnu/'+lib)
    if args.tiktoken_cache:
        mount(command, args.tiktoken_cache.resolve(), '/opt/agenttx-tiktoken-cache')
        command += ['-e', 'TIKTOKEN_CACHE_DIR=/opt/agenttx-tiktoken-cache']
    env = dict(AGENTTX_MOTIVATION_BWRAP='1', AGENTTX_NO_PROXY='1',
               AGENTTX_TRACE_SPOOL_ROOT='/var/tmp', AGENTTX_SKIP_ORPHAN_REAP='1',
               AGENTTX_WORKER_PYTHON=str(args.python), PYTHONDONTWRITEBYTECODE='1',
               AGENTTX_RESULT_UID=str(os.getuid()), AGENTTX_RESULT_GID=str(os.getgid()),
               PYTHONPATH=f'{ROOT}/src:{ROOT}', SWE_AGENT_LOG_STREAM_LEVEL='DEBUG',
               CODEX_BIN='/usr/local/bin/codex',
               AGENTTX_RECOVERY_CONTEXT=CONTEXTS[cell.name],
               AGENTTX_FAULT_SEED=str(getattr(args, 'fault_seed', '20261019')),
               PIP_INDEX_URL='https://mirrors.aliyun.com/pypi/simple',
               UV_INDEX_URL='https://mirrors.aliyun.com/pypi/simple',
               UV_CACHE_DIR='/results/cache/uv', PIP_CACHE_DIR='/results/cache/pip',
               UV_PYTHON_DOWNLOADS='never', UV_HTTP_TIMEOUT='40',
               CODEX_CONFIG_FILE=str(args.profile_dir/'config.toml'),
               CODEX_AUTH_FILE=str(args.profile_dir/'auth.json'),
               AGENTTX_SWE_PROFILE=str(args.profile_dir/'sweagent.toml'))
    if task_user:env['AGENTTX_TB_TASK_USER']=task_user
    if portable_git:env['AGENTTX_TB_GIT_FALLBACK']='/opt/agenttx-prerequisites/git'
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
                '--timeout', str(args.timeout), '--verifier-timeout', str(args.verifier_timeout),
                '--fault-seed', str(getattr(args, 'fault_seed', '20261019'))]
    if getattr(args, 'verifier_setup', None):
        command += ['--prepared-verifier']
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
        context_variant='per_policy', contexts=CONTEXTS, retained_history_call_limit=6,
        fault_seed=str(getattr(args, 'fault_seed', '20261019')),
        wall_timeout=args.timeout, verifier_timeout=args.verifier_timeout, harness=args.harness,
        task=args.task, policies=args.policies, preflight_only=args.preflight_only,
        completion_contract=COMPLETION_CONTRACT, success_policy=SUCCESS_POLICY)
    (args.output_dir/'registration.json').write_text(json.dumps(registration,indent=2))
    try:
        task = tb.load_tasks(args.cache_dir)[args.task]
        source = tb.task_source(args.cache_dir,task)
        tests, test_layout = official_tests_directory(source, args.output_dir)
        image, _ = tb.ensure_official_image(task,args.cache_dir)
        identity = image_identity(image,allow_task_user=getattr(args,'task_user_adapter',False))
        from experiments.scripts.tb_worker_compat import worker_compatibility
        args.worker_compatibility=worker_compatibility(args.task,identity['id'],args.python)
        if args.worker_compatibility:
            registration['worker_compatibility']=args.worker_compatibility[2]
        from experiments.scripts.prepare_tb_portable_git import verified_prerequisite
        args.portable_git,args.portable_git_wrapper,registration['coordinator_git']=verified_prerequisite()
        script, local_uv = tb.prepare_official_test_script(source,args.cache_dir)
        args.verifier_mounts=[]; args.verifier_setup=None
        evidence_path=getattr(args, 'verifier_evidence', None)
        if evidence_path:
            evidence=json.loads(evidence_path.read_text())
            baseline_audited=False
            if evidence.get('task_baseline_audit'):
                from experiments.scripts.audit_tb_task_baselines import validate_audit
                baseline_audited=validate_audit(evidence,source)
            if evidence.get('task') != args.task or not (evidence.get('official_tests_executed') or baseline_audited):
                raise ValueError('verifier evidence must identify this task and executed official tests')
            registration['task_baseline_audited']=baseline_audited
            folder=evidence_path.parent
            args.verifier_setup=args.output_dir/'verifier-setup.sh'
            args.verifier_setup.write_text((folder/'run.sh').read_text().replace('/results/baseline','/results/verifier'))
            original_command=json.loads((folder/'command.json').read_text())
            for item in original_command:
                if not item.startswith('type=bind,'):continue
                fields=dict(part.split('=',1) for part in item.split(',') if '=' in part)
                dest=fields.get('dst','')
                if dest.startswith('/probe/'):
                    args.verifier_mounts.append((Path(fields['src']),dest))
            registration['verifier_evidence_sha256']=file_hash(evidence_path)
            registration['verifier_setup_sha256']=file_hash(args.verifier_setup)
        else:
            from experiments.scripts.validate_tb_bootstrap_batch import effective_script, SETUP, TOOLS
            effective,versions=effective_script((source/'run-tests.sh').read_text(),args.task)
            script=args.output_dir/'effective-run-tests.sh';script.write_text(effective)
            args.verifier_setup=args.output_dir/'verifier-setup.sh'
            args.verifier_setup.write_text(SETUP.replace('/results/baseline','/results/verifier'))
            args.verifier_mounts=[(script,'/probe/effective-run-tests.sh'),(tests,'/probe/tests')]
            for version in {v['resolved'] for v in versions}:
                binary=TOOLS/'bin/uv' if version=='0.7.13' else TOOLS/f'uv-{version}/bin/uv'
                args.verifier_mounts.append((binary,'/probe/uv-'+version))
        local_uv = local_uv or args.uv
        if local_uv is None:
            raise ValueError('provide --uv for the official test bootstrap')
        task_file = args.output_dir/'task.json'
        task_file.write_text(json.dumps(dataclasses.asdict(task),indent=2))
        registration.update(image=identity, tests=tree_manifest(tests), test_layout=test_layout,
            original_test_script_sha256=file_hash(source/'run-tests.sh'),
            effective_test_script_sha256=file_hash(script), uv_sha256=file_hash(local_uv),
            profile_sha256=file_hash(args.profile_dir/('sweagent.toml' if args.harness=='sweagent' else 'config.toml')),
            codex_sha256=file_hash(args.codex_bin), code_mode_host_sha256=file_hash(args.codex_bin.parent/'codex-code-mode-host'))
        (args.output_dir/'registration.json').write_text(json.dumps(registration,indent=2))
        try:
            for policy in args.policies:
                cell = args.output_dir/policy
                cell.mkdir(mode=0o700)
                from experiments.scripts.tb_task_services import TaskServices
                services=TaskServices(source,args.task,cell,identity['id']).start()
                try:command = services.apply(build_command(args,identity,cell,task_file,script,tests,local_uv))
                except Exception:
                    services.close();raise
                (cell/'container-command.json').write_text(json.dumps(command,indent=2))
                start=time.perf_counter()
                rc=None; error=None
                try:
                    with (cell/'container.stdout.log').open('w') as out, (cell/'container.stderr.log').open('w') as err:
                        result=subprocess.run(command,stdout=out,stderr=err,
                            timeout=2*args.timeout+args.verifier_timeout+300,check=False)
                    rc=result.returncode
                except subprocess.TimeoutExpired:
                    error='container wall deadline exceeded'
                finally:
                    cidfile=cell/'container.cid'
                    if cidfile.exists():
                        cid=cidfile.read_text().strip()
                        # Only stop our own container, never enumerate/kill others.
                        subprocess.run(['docker','rm','-f',cid],capture_output=True,check=False,timeout=30)
                    services.close()
                path=cell/'result.json'
                row=json.loads(path.read_text()) if path.exists() else dict(completed=False,status='infrastructure_error')
                row.update(container_returncode=rc,host_error=error,container_wall_s=time.perf_counter()-start,
                           policy=policy,image_id=identity['id'])
                if rc not in (0, 1) and row.get('completed'):
                    row.update(completed=False,status='infrastructure_error')
                row['api_usage'] = logged_usage(cell, args.harness, row)
                (cell/'host-result.json').write_text(json.dumps(row,indent=2))
                rows.append(row)
                print(json.dumps({k:row.get(k) for k in ('policy','status','completed','error','host_error')}),flush=True)
        finally:
            # Keep the effective script as launch provenance.
            pass
    except Exception as exc:
        rows.append(dict(status='infrastructure_error',completed=False,error=f'{type(exc).__name__}: {exc}'))
        (args.output_dir/'host-error.txt').write_text(traceback.format_exc())
    (args.output_dir/'summary.json').write_text(json.dumps(rows,indent=2))
    return 0 if rows and all(r['completed'] for r in rows) else 1


def logged_usage(cell, harness, row):
    if harness == 'codex':
        usage = dict(source='codex_stdout_jsonl',
                    main_tokens=row.get('total_tokens', 0), replay_tokens=row.get('doc_replay_tokens', 0),
                    total_tokens=row.get('total_tokens', 0) + row.get('doc_replay_tokens', 0),
                    complete=bool(row.get('usage_source') == 'stdout_jsonl' and row.get('model_calls', 0)
                                  and (not row.get('doc_replay_needed') or row.get('doc_replay_usage_source') == 'stdout_jsonl')))
        if not usage['complete']:
            usage.update(main_tokens=None, replay_tokens=None, total_tokens=None,
                         partial_session_usage=codex_partial_session_usage(cell, row),
                         reason='Final usage missing; session records are a lower bound, including possible in-flight requests.')
        return usage
    text=re.sub(r'\s+', '', '\n'.join(p.read_text(errors='replace') for p in
        (cell/'container.stdout.log',cell/'container.stderr.log') if p.exists()))
    records=list(re.finditer(r'usage=Usage\(completion_tokens=(\d+),prompt_tokens=(\d+),total_tokens=(\d+)', text))
    expected=row.get('model_calls',0)+row.get('doc_replay_model_calls',0)
    return dict(source='logged_api_usage',records=len(records),expected_calls=expected,
                total_tokens=sum(int(m[3]) for m in records),
                complete=bool(records) and len(records)==expected
                         and all(int(m[1])+int(m[2])==int(m[3]) for m in records))


def codex_partial_session_usage(cell, row):
    """Recover recorded response usage without claiming a timed-out bill is complete."""
    workspace = Path(row.get('workspace', '/'))
    if not workspace.is_absolute() or workspace == Path('/') or '..' in workspace.parts:
        return dict(recorded_tokens=0, records=0, complete=False)
    base = Path(cell)/'session/upperdir'/str(workspace).lstrip('/')
    records = {}; evidence = []
    for path in sorted(base.glob('.agenttx-codex-*/codex/sessions/*/*/*/*.jsonl')):
        found = False
        for line in path.read_text(errors='replace').splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            if event.get('type') != 'token_usage_record':
                continue
            payload = event.get('payload', {})
            response_id = payload.get('response_id')
            values = payload.get('usage', {})
            counts = tuple(values.get(k) for k in ('input_tokens', 'output_tokens', 'total_tokens'))
            if not response_id or not all(isinstance(v, int) and v >= 0 for v in counts):
                continue
            if counts[0] + counts[1] != counts[2]:
                continue
            if response_id in records and records[response_id] != counts:
                return dict(recorded_tokens=None, records=len(records), complete=False,
                            error='conflicting_response_usage')
            records[response_id] = counts
            found = True
        if found:
            evidence.append(dict(path=str(path.relative_to(cell)), sha256=file_hash(path)))
    return dict(recorded_tokens=sum(v[2] for v in records.values()), records=len(records),
                complete=False, source='codex_session_response_usage', evidence=evidence)


def run_inside(args):
    from agenttx.harness import CodingAgentHarness
    from agenttx.policy import CommitPolicy
    from experiments.scripts.bench_official_tasks import (_create_workload_harness, _apply_blind_policy,
        _randomized_crash_docs, _auto_localize_policy, _apply_policy)
    from experiments.workloads.recovery_inject import (document_text_valid, missing_independent_docs,
        independent_work_discarded, doc_replay_prompt, read_recovery_documents,
        build_recovery_manifest, render_recovery_manifest_prompt)
    output=args.output_dir; workspace=Path.cwd()
    task=tb.TBTask(**json.loads(args.task_file.read_text()))
    row=dict(protocol=PROTOCOL,protocol_validation_only=True,formal_eligible=False,
             task=task.task_id,policy=args.policy,harness=args.harness,
             model=args.model,reasoning_effort='max',count_limit=None,
             context_variant=CONTEXTS[args.policy],recovery_context_variant=CONTEXTS[args.policy],
             retained_history_call_limit=6 if CONTEXTS[args.policy]=='compact' else None,
             fault_seed=args.fault_seed,doc_replay_tokens=0,doc_replay_model_calls=0,
             doc_replay_tool_calls=0,model_calls=0,
             workspace=str(workspace),agent_runtime='official_task_container',
             verifier_runtime='same_official_task_container',timings={})
    started=time.perf_counter(); h=None
    try:
        os.environ['AGENTTX_RECOVERY_CONTEXT']=CONTEXTS[args.policy]
        os.environ['AGENTTX_FAULT_SEED']=args.fault_seed
        row['runtime_tools']=prepare_runtime_tools(output)
        for tool in ('git','strace'):
            subprocess.run([tool,'--version'],check=True,capture_output=True)
        task_python=subprocess.run(['python3','-c','import sys;print(sys.executable);print(sys.version)'],
                                   capture_output=True,text=True,check=True).stdout
        row['task_python']=task_python
        row['worker_python']=sys.executable
        task_user=os.environ.get('AGENTTX_TB_TASK_USER') or None
        row['original_task_user']=task_user or 'root'
        h=CodingAgentHarness(workspace,session_dir=output/'session',trace_backend='strace',
            tool_user=task_user,
            policy=CommitPolicy(workspace,allow_external_writes=True,ephemeral_roots=(Path('/root'),Path('/tmp'))))
        tx=h.tx
        prime_resolver(h)
        gate=tx.run_tool('runtime-preflight',['bash','-c','printf runtime-ok'],trace_reads=True,timeout_s=30)
        if gate.returncode or gate.stdout.strip()!='runtime-ok':
            raise RuntimeError(f'task image transaction preflight failed: {gate.stderr}')
        # Preflight is infrastructure, not part of the task trajectory.
        tx.rollback(gate.step_id)
        if args.preflight_only:
            # Exercise the real adapter imports as well as the transaction.
            # A shell-only check misses native-extension/glibc incompatibility.
            adapter=_create_workload_harness(args.harness,harness_root=None,model=args.model,harness_command=None,
                harness_timeout_s=args.timeout,output_dir=output/'adapter-import-preflight',
                max_turns=None,reasoning_effort='max')
            row['adapter_import_preflight']=True
            if args.harness=='sweagent':row['sweagent_tool_limits']=adapter.public_config['tool_limits']
            row['completion_contract_preflight']=preflight_completion_contract(h)
            row.update(completed=True,status='preflight_passed',official_success=None)
        else:
            docs, fault_after, trial_seed=_randomized_crash_docs(task,task.task_id,0)
            if args.policy=='clean_recovery': docs=[]
            row.update(fault_trial_seed=trial_seed,fault_after_independent_steps=fault_after,
                       fault_injected=args.policy!='clean_recovery')
            task_text=task.instruction
            tx.conversation.seed('You are a coding agent. Complete the original task using shell tools.',task_text)
            injected={}; targets=[]; localization={}
            if args.policy!='clean_recovery':
                agent=type('Agent',(),{'harness':h})()
                injected=tb.inject_task_trajectory(agent,task,sys.executable,docs=docs,fault_after_docs=fault_after)
                if not injected['tests_failed']:
                    raise RuntimeError('injected fault did not fail the crash contract')
                precrash=tx.conversation.to_dict()
                stamp=time.perf_counter()
                if args.policy=='causal':
                    targets,localization=_auto_localize_policy(agent,injected)
                elif args.policy=='ideal_oracle':
                    targets=_apply_policy(agent,args.policy,injected['root_step'],
                                          oracle_targets=injected['oracle_invalid_steps'])
                    localization=dict(localized_root_steps=[injected['root_step']],
                        fault_location_source='oracle',fault_location_disclosed=True,
                        localization_validation='oracle_truth')
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
            def adapter_for(folder):
                return _create_workload_harness(args.harness,harness_root=None,model=args.model,harness_command=None,
                    harness_timeout_s=args.timeout,output_dir=folder,max_turns=None,reasoning_effort='max')
            missing=missing_independent_docs(workspace,docs,agent=agent) if injected and independent_work_discarded(injected,tx.ledger) else []
            row['doc_replay_needed']=bool(missing)
            if missing:
                replay_dir=output/'replay'; replay_dir.mkdir()
                replay=adapter_for(replay_dir).run_in_transaction(h,doc_replay_prompt(docs=missing,task_name=task.task_id))
                (output/'replay-result.json').write_text(json.dumps(dataclasses.asdict(replay),indent=2))
                row.update(doc_replay_tokens=replay.total_tokens,doc_replay_prompt_tokens=replay.prompt_tokens,
                    doc_replay_completion_tokens=replay.completion_tokens,doc_replay_tool_calls=replay.tool_calls,
                    doc_replay_usage_source=replay.usage_source,doc_replay_model_calls=model_call_count(replay))
            prompt=task_text
            if injected:
                state_paths=[injected['faulty_path'],*injected.get('derived_paths',[])]
                manifest=build_recovery_manifest(policy=args.policy,ledger=tx.ledger,injected=injected,docs=docs,
                    document_contents=read_recovery_documents(agent,docs),workdir=workspace,rollback_targets=targets,
                    path_exists={p:tx.path_exists(workspace/p) for p in state_paths},
                    oracle_fault_step_id=injected['root_step'] if args.policy=='ideal_oracle' else None,
                    localized_fault_step_ids=localization.get('localized_root_steps',[]) if args.policy=='causal' else (),
                    fault_location_source=localization.get('fault_location_source','hidden'))
                if not manifest['authoritative']:raise RuntimeError('recovery manifest state mismatch')
                (output/'recovery-manifest.json').write_text(json.dumps(manifest,indent=2))
                prompt+='\n\nA crash occurred. Resume the original task.\n'+render_recovery_manifest_prompt(manifest)
            prompt=completion_prompt(prompt)
            (output/'prompt.txt').write_text(prompt)
            model_dir=output/'model';model_dir.mkdir()
            adapter=adapter_for(model_dir)
            if args.harness=='sweagent':row['sweagent_tool_limits']=adapter.public_config['tool_limits']
            stamp=time.perf_counter()
            if args.harness=='sweagent':
                # Clean tasks have no tool trajectory to seed or reconstruct.
                context = (dict(original_task=task_text,precrash_conversation=precrash,context_variant=CONTEXTS[args.policy])
                           if injected else None)
                result=adapter.run_in_transaction(h,prompt,native_context=context)
            else:
                result=adapter.run_in_transaction(h,prompt+'\n'+json.dumps(recovery_messages(tx.conversation,args.policy)))
            row['timings']['model_wall_s']=time.perf_counter()-stamp
            (output/'model-result.json').write_text(json.dumps(dataclasses.asdict(result),indent=2))
            row.update(model_returncode=result.returncode,prompt_tokens=result.prompt_tokens,
                       completion_tokens=result.completion_tokens,total_tokens=result.total_tokens,
                       usage_source=result.usage_source,tool_calls=result.tool_calls,model_calls=model_call_count(result))
            tx.dump_ledger(output/'final-ledger.json')
            # Grade the same private filesystem view that the agent used.
            # Container-local package installation may touch /usr or caches
            # outside the task workdir; publishing those paths is unnecessary
            # for grading and remains forbidden by the normal commit policy.
            agent=type('Agent',(),{'harness':h})()
            contents=read_recovery_documents(agent,docs) if docs else {}
            row['documents_valid']=all(document_text_valid(contents.get(d.path,''),d.prefix,d.lines) for d in docs)
            row['derived_removed']=not tx.path_exists(workspace/'recovery_build/derived.txt')
            row['model_finished']=result.returncode==0
            row.update(model_timed_out=result.returncode==124,
                       completion_contract=COMPLETION_CONTRACT, success_policy=SUCCESS_POLICY,
                       model_session_wall_s=args.timeout, verifier_wall_s=args.verifier_timeout)
        if not args.preflight_only:
            stamp=time.perf_counter()
            restore=run_trusted_verifier_tool(tx,'restore-official-test-assets',[
                'bash','-c','rm -rf /tests && mkdir /tests && cp -a /opt/agenttx-verifier/tests/. /tests/'],
                60)
            if restore.returncode:raise RuntimeError('official test asset restore failed: '+restore.stderr[-2000:])
            verifier='/opt/agenttx-verifier/setup.sh' if args.prepared_verifier else '/opt/agenttx-verifier/run-tests.sh'
            rc=verify_transaction_view(tx,['env','TEST_DIR=/tests','bash',verifier],output,args.verifier_timeout)
            row['timings']['verification_wall_s']=time.perf_counter()-stamp
            row['verifier_filesystem']='same_agent_transaction_view'
            row['verifier_user']='root'
            row['publication_performed']=False
            tx.dump_ledger(output/'verification-ledger.json')
            row['official_tests_rc']=rc
            row['official_success']=rc==0
            row['success']=outcome_success(result.returncode,rc,row['documents_valid'] and row['derived_removed'])
            row['completed'],row['status']=outcome_status(result.returncode,rc,row['documents_valid'] and row['derived_removed'])
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
    return 0 if row.get('completed') else 1


def run_trusted_verifier_tool(tx,name,command,timeout):
    # As in the original official grading subprocess, the trusted verifier
    # uses the container coordinator account. Agent tools keep the image user.
    pool=getattr(tx,'pool',None)
    original_user=getattr(pool,'tool_user',None)
    try:
        if pool is not None:pool.tool_user=None
        return tx.run_tool(name,command,trace_reads=False,timeout_s=timeout)
    finally:
        if pool is not None:pool.tool_user=original_user


def verify_transaction_view(tx,command,output,timeout):
    """Observe private task state without publishing container system effects."""
    try:
        verdict=run_trusted_verifier_tool(tx,'official-verifier',command,timeout)
        stdout,stderr,rc=verdict.stdout,verdict.stderr,verdict.returncode
    except subprocess.TimeoutExpired as exc:
        def decoded(value):return value.decode(errors='replace') if isinstance(value,bytes) else value or ''
        stdout,stderr,rc=decoded(exc.stdout),decoded(exc.stderr),124
    (output/'verifier.stdout.log').write_text(stdout)
    (output/'verifier.stderr.log').write_text(stderr)
    return rc


def model_call_count(result):
    metadata=getattr(result,'metadata',{})
    return int(metadata.get('sweagent_model_calls',0)) if metadata.get('sweagent_model_calls') is not None else sum(
        e.get('type')=='turn.completed' for e in getattr(result,'events',[]))


def prepare_runtime_tools(output):
    if os.environ.get('AGENTTX_TB_PORTABLE_GIT')=='1':
        subprocess.run(['git','--version'],check=True,capture_output=True)
        return dict(git='read-only portable prerequisite; task networking/APT/curl unmodified')
    if shutil.which('git'):
        return dict(git='image-provided')
    fallback=os.environ.get('AGENTTX_TB_GIT_FALLBACK')
    if fallback:
        prerequisite=Path(fallback)
        if not prerequisite.is_absolute() or not prerequisite.is_file():
            raise RuntimeError('verified local Git prerequisite is missing')
        subprocess.run([str(prerequisite),'--version'],check=True,capture_output=True,timeout=30)
        # Original Git is kept whenever available. Never replace an occupied
        # task path, even a broken symlink; that needs a task-specific audit.
        if COORDINATOR_GIT.exists() or COORDINATOR_GIT.is_symlink():
            raise RuntimeError('missing executable Git but coordinator path is occupied')
        COORDINATOR_GIT.symlink_to(prerequisite)
        return dict(git='verified local fallback; image originally has no Git; no network installation')
    # Both native agents require git for their session metadata. Install only
    # the missing coordinator prerequisite, before creating the task overlay.
    command=r'''set -eu
export DEBIAN_FRONTEND=noninteractive
for source in /etc/apt/sources.list /etc/apt/sources.list.d/*.sources /etc/apt/sources.list.d/*.list; do
 [ -f "$source" ] || continue
 sed -i -E 's|https?://deb.debian.org/debian|https://mirrors.aliyun.com/debian|g; s|https?://security.debian.org/debian-security|https://mirrors.aliyun.com/debian-security|g; s|https?://archive.ubuntu.com/ubuntu|https://mirrors.aliyun.com/ubuntu|g; s|https?://security.ubuntu.com/ubuntu|https://mirrors.aliyun.com/ubuntu|g' "$source"
done
printf 'Acquire::Retries "1"; Acquire::http::Timeout "20"; Acquire::https::Timeout "20";\n' > /etc/apt/apt.conf.d/99agenttx-runtime
apt-get update -qq
apt-get install -y --no-install-recommends git
'''
    with (output/'runtime-tools.log').open('w') as log:
        subprocess.run(['bash','-c',command],stdout=log,stderr=subprocess.STDOUT,check=True,timeout=240)
    return dict(git='installed missing agent prerequisite using domestic apt mirror')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    sub=parser.add_subparsers(dest='command',required=True)
    host=sub.add_parser('host'); inside=sub.add_parser('inside')
    for p in (host,inside):
        p.add_argument('--output-dir',type=Path,required=True)
        p.add_argument('--harness',choices=('codex','sweagent'),required=True)
        p.add_argument('--model',default='gpt-5.6-luna')
        p.add_argument('--timeout',type=float,default=MODEL_TIMEOUT_S)
        p.add_argument('--verifier-timeout',type=float,default=VERIFIER_TIMEOUT_S)
        p.add_argument('--preflight-only',action='store_true')
        p.add_argument('--fault-seed',default='20261019')
    host.add_argument('--task-user-adapter',action='store_true',help='Keep original named task user for every tool while root coordinates namespaces')
    host.add_argument('--static-strace',type=Path,help='Verified static tracer for glibc older than 2.28; use dynamic bundle for nonroot tasks')
    host.add_argument('--task',required=True)
    host.add_argument('--policies',nargs='+',choices=POLICIES,default=list(POLICIES))
    host.add_argument('--cache-dir',type=Path,required=True)
    host.add_argument('--python',type=Path,default=Path(sys.executable))
    host.add_argument('--profile-dir',type=Path,required=True)
    host.add_argument('--swe-agent-root',type=Path)
    host.add_argument('--codex-bin',type=Path,required=True)
    host.add_argument('--uv',type=Path)
    host.add_argument('--tiktoken-cache',type=Path)
    host.add_argument('--verifier-evidence',type=Path)
    inside.add_argument('--task-file',type=Path,required=True)
    inside.add_argument('--policy',choices=POLICIES,required=True)
    inside.add_argument('--prepared-verifier',action='store_true')
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
