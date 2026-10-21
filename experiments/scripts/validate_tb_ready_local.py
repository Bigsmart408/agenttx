#!/usr/bin/env python3
"""Model-free, image-native diagnostics for already-built local TB images.

Original tests and images are immutable. The test probe uses the image's own
Python plus a separately pinned pytest bootstrap; it is deliberately not
reported as a complete run of the upstream run-tests.sh bootstrap.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import socket
import subprocess
import sys
import time
import traceback
import uuid
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]
DATA = Path('/mnt/data/agenttx-runtime')
sys.path[:0] = [str(ROOT/'src'), str(ROOT)]


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def classify_junit(path, returncode):
    """Assertions in an unsolved baseline are valid diagnostic outcomes."""
    if returncode == 124:
        return 'timeout', {}
    if not path.exists():
        return 'no_test_report', {}
    root = ET.parse(path).getroot()
    cases = list(root.iter('testcase'))
    errors = sum(len(list(c.iter('error'))) for c in cases)
    failures = sum(len(list(c.iter('failure'))) for c in cases)
    skipped = sum(len(list(c.iter('skipped'))) for c in cases)
    counts = dict(tests=len(cases), errors=errors, failures=failures, skipped=skipped)
    if any(f.get('type', '').endswith(('ModuleNotFoundError', 'ImportError'))
           for case in cases for f in case.iter('failure')):
        return 'test_dependency_missing', counts
    if errors:
        return 'test_setup_or_collection_error', counts
    if not cases or len(cases) == skipped:
        return 'no_executed_tests', counts
    if returncode == 0:
        return 'baseline_passed', counts
    if returncode == 1 and failures:
        return 'baseline_failed', counts
    return 'test_runner_error', counts


def inside(output):
    """Run in a disposable task container, without credentials or model code."""
    from agenttx.harness import CodingAgentHarness
    from agenttx.policy import CommitPolicy
    from experiments.scripts.bench_tb_image_native import prime_resolver

    row = dict(started_at=now(), workspace=str(Path.cwd()), uid=os.getuid(),
               worker_python=sys.executable, model_calls=0, gold_solution_used=False,
               agent_runtime='original_task_image', verifier_runtime='same_task_container')
    task_user = os.environ.get('AGENTTX_TB_TASK_USER') or None
    h = None
    marker = Path.cwd()/('.agenttx-env-check-' + uuid.uuid4().hex)
    try:
        row['task_commands'] = {name:shutil.which(name) for name in
            ('python', 'python3', 'node', 'npm', 'R', 'ruby', 'gcc', 'git', 'bash')}
        row['workspace_entries'] = sorted(p.name for p in Path.cwd().iterdir())[:200]
        row['task_python'] = subprocess.run(['python3', '--version'], text=True,
            capture_output=True, timeout=10).stdout.strip() if shutil.which('python3') else None
        syntax = subprocess.run(['bash', '-n', '/probe/run-tests.sh'], capture_output=True,
                                text=True, timeout=10)
        row['official_script_syntax_ok'] = syntax.returncode == 0
        (output/'script-syntax.stderr').write_text(syntax.stderr)
        if os.getuid() != 0:
            raise RuntimeError('image-native AgentTX currently requires root; image user was preserved')
        h = CodingAgentHarness(Path.cwd(), session_dir=output/'session', trace_backend='strace',
            tool_user=task_user,
            policy=CommitPolicy(Path.cwd(), allow_external_writes=True,
                                ephemeral_roots=(Path('/root'), Path('/tmp'))))
        prime_resolver(h)
        if task_user:
            import pwd
            expected = pwd.getpwnam(task_user)
            row['original_task_identity'] = dict(user=task_user, uid=expected.pw_uid, gid=expected.pw_gid)
            writable=subprocess.run(['runuser','-u',task_user,'--','test','-w',str(Path.cwd())]).returncode==0
            row['original_workdir_writable']=writable
            if not writable:marker=Path(expected.pw_dir)/marker.name
            secret=Path('/var/tmp/.agenttx-root-only-'+uuid.uuid4().hex)
            secret.write_text('coordinator-only');secret.chmod(0o600)
            try:
                for traced in (True,False):
                    identity=h.tx.run_tool('task-user-check', ['bash','-c',
                        'id -u; id -g; printf "%s\\n" "$HOME"; test ! -r '+shlex.quote(str(secret))],
                        trace_reads=traced,timeout_s=20)
                    assert identity.returncode==0, identity.stderr
                    assert identity.stdout.splitlines()==[str(expected.pw_uid),str(expected.pw_gid),expected.pw_dir], identity.stdout
                row['task_user_permissions_verified']=True
            finally:secret.unlink(missing_ok=True)
        target = shlex.quote(str(marker))
        record = h.tx.run_tool('environment-write', ['bash', '-c',
            f'printf agenttx-probe > {target}; cat {target}'],
            trace_reads=True, timeout_s=20)
        assert record.returncode == 0 and record.stdout.strip() == 'agenttx-probe', record.stderr
        assert not marker.exists(), 'sandbox write leaked before commit'
        row['write_isolated'] = True
        h.tx.rollback(record.step_id)
        verify = h.tx.run_tool('environment-rollback', ['bash', '-c',
            f'test ! -e {target} && printf rollback-ok'],
            trace_reads=True, timeout_s=20)
        assert verify.returncode == 0 and verify.stdout.strip() == 'rollback-ok', verify.stderr
        row['rollback_verified'] = True
        if task_user:
            again=h.tx.run_tool('environment-commit-user', ['bash','-c',
                f'printf agenttx-probe > {target}'],trace_reads=False,timeout_s=20)
            assert again.returncode==0,again.stderr
            h.tx.commit()
            assert marker.read_text()=='agenttx-probe' and marker.stat().st_uid==expected.pw_uid
            row['commit_file_owner_verified']=True
            row['session_task_user_persisted']=json.loads((h.tx.pool.session_dir/'agenttx.json').read_text())['tool_user']==task_user
            marker.unlink()
            from agenttx.runtime import AgentTX
            session=h.tx.pool.session_dir
            h.tx.close(destroy=False)
            h.tx=AgentTX.load(session)
            resumed=h.tx.run_tool('restored-task-user',['id','-u'],trace_reads=True,timeout_s=20)
            assert resumed.returncode==0 and resumed.stdout.strip()==str(expected.pw_uid), resumed.stderr
            row['session_task_user_restored']=True
        h.tx.dump_ledger(output/'ledger.json')
        h.close(destroy=True)
        h = None
        row['transaction_ok'] = True
    except Exception as exc:
        row.update(transaction_ok=False, transaction_error=type(exc).__name__ + ': ' + str(exc))
        (output/'transaction-error.txt').write_text(traceback.format_exc())
    finally:
        if h is not None:
            try: h.close(destroy=True)
            except Exception: pass
        marker.unlink(missing_ok=True)
    # Use the task interpreter, never the mounted coordinator interpreter, for
    # unchanged official tests. Dependency/bootstrap gaps remain explicit.
    if os.environ.get('AGENTTX_TB_EMBEDDED_TESTS') == '1':
        row['baseline_classification'] = 'embedded_official_script_required'
    elif shutil.which('python3'):
        env = {**os.environ, 'PYTHONPATH':'/probe/pytest', 'TEST_DIR':'/tests',
               'PYTEST_DISABLE_PLUGIN_AUTOLOAD':'1'}
        for key in ('OPENAI_API_KEY', 'ANTHROPIC_API_KEY', 'CODEX_CONFIG_FILE', 'CODEX_AUTH_FILE'):
            env.pop(key, None)
        command = ['python3', '-m', 'pytest', '/tests', '-q', '-rA', '--maxfail=1',
                   '-p', 'no:cacheprovider', '--junitxml=/results/baseline.xml']
        report=None
        if task_user:
            import tempfile
            report=Path(tempfile.mkdtemp(prefix='agenttx-report-',dir='/var/tmp'))
            os.chown(report,expected.pw_uid,expected.pw_gid)
            command=[v.replace('/results/baseline.xml',str(report/'baseline.xml')) for v in command]
            command=['runuser','-u',task_user,'--',*command]
        row['baseline_command'] = command
        start = time.monotonic()
        with (output/'baseline.stdout').open('w') as out, (output/'baseline.stderr').open('w') as err:
            try:
                completed = subprocess.run(command, env=env, stdout=out, stderr=err,
                                           timeout=45, start_new_session=True)
                rc = completed.returncode
            except subprocess.TimeoutExpired:
                rc = 124
        if report and (report/'baseline.xml').exists():shutil.copy2(report/'baseline.xml',output/'baseline.xml')
        row['baseline_wall_s'] = time.monotonic() - start
        row['baseline_returncode'] = rc
        row['baseline_classification'], row['baseline_tests'] = classify_junit(output/'baseline.xml', rc)
        if os.environ.get('AGENTTX_TB_PROBE_TASK') == 'hello-world' and rc == 1:
            # Directly implement the public one-line task instruction, without
            # loading a reference solution. Check that the same tests turn green.
            hello = Path.cwd()/'hello.txt'
            assert not hello.exists()
            hello.write_text('Hello, world!\n')
            positive = [v.replace('baseline.xml', 'positive.xml') for v in command]
            with (output/'positive.stdout').open('w') as out, (output/'positive.stderr').open('w') as err:
                verdict = subprocess.run(positive, env=env, stdout=out, stderr=err, timeout=20)
            row['public_instruction_positive_rc'] = verdict.returncode
            row['public_instruction_positive_tests'] = classify_junit(output/'positive.xml', verdict.returncode)[1]
            hello.unlink()
    else:
        row['baseline_classification'] = 'task_python_missing'
    row['finished_at'] = now()
    save(output/'inside-result.json', row)


def bind(command, source, destination=None, readonly=True):
    source = Path(source).absolute()
    if not source.exists():
        raise FileNotFoundError(source)
    command += ['--mount', f'type=bind,src={source},dst={destination or source}' + (',readonly' if readonly else '')]


def probe(record, out, tools, tasks, task_user_adapter=False, static_strace=None):
    from experiments.workloads import terminal_bench_suite as tb
    import yaml
    folder = out/record['task']
    folder.mkdir()
    start = time.monotonic()
    row = dict(task=record['task'], started_at=now(), image_id=record['local_image_id'],
               model_calls=0, gold_solution_used=False)
    name = 'agenttx-tb-probe-' + uuid.uuid4().hex
    try:
        source = tb.task_source(DATA/'cache', tasks[record['task']])
        compose = yaml.safe_load((source/'docker-compose.yaml').read_text())
        services = compose.get('services', {})
        row['compose_services'] = sorted(services)
        row['requires_additional_services'] = len(services) > 1
        row['test_assets'] = {str(p.relative_to(source/'tests')):digest(p)
                              for p in sorted((source/'tests').rglob('*')) if p.is_file()}
        script_text = (source/'run-tests.sh').read_text()
        embedded = ('cat > /app/test_patch.diff' in script_text
                    and 'patch --fuzz=5 -p1 -i /app/test_patch.diff' in script_text
                    and 'pytest ' in script_text)
        row['test_layout'] = 'inline_patch_in_official_script' if embedded else 'tests_directory'
        if not row['test_assets'] and not embedded:
            raise RuntimeError('official test assets missing')
        row['official_script_sha256'] = digest(source/'run-tests.sh')
        info = json.loads(subprocess.check_output(['docker', 'image', 'inspect', record['tag']],
                                                  text=True, timeout=20))[0]
        assert info['Id'] == row['image_id'], 'recorded image identity changed'
        workdir = info['Config'].get('WorkingDir') or ''
        assert workdir.startswith('/') and workdir != '/', 'invalid image workdir'
        row['image_workdir'] = workdir
        row['image_user'] = info['Config'].get('User') or 'root'
        row['image_architecture'] = info['Architecture']
        assert info['Architecture'] == 'amd64' and info['Os'] == 'linux'
        task_user=None
        if row['image_user'] not in {'root','0','0:0'} and task_user_adapter:
            task_user=row['image_user'].split(':')[0]
        if row['image_user'] not in {'root','0','0:0'} and not task_user_adapter:
            raise RuntimeError('Image user '+row['image_user']+' preserved; current AgentTX transaction adapter requires root.')
        command = ['docker', 'run', '--rm', '--pull=never', '--name', name,
                   '--label', 'agenttx.tb-validation='+out.name,
                   '--privileged', '--network=none', '--cpus=1', '--memory=2g',
                   '--tmpfs', '/tmp:exec', '--workdir', workdir]
        if task_user:
            command += ['--user','0','-e','AGENTTX_TB_TASK_USER='+task_user]
            row['namespace_coordinator_user']='root'
            row['task_tool_user']=task_user
        prefixes = {Path(sys.prefix), Path(sys.base_prefix), ROOT}
        # Preserve both the symlink spelling and canonical interpreter prefix.
        link = Path(sys.executable)
        if link.is_symlink():
            target = Path(os.readlink(link))
            if not target.is_absolute(): target = link.parent/target
            prefixes.add(target.parent.parent.absolute())
        for prefix in sorted(prefixes): bind(command, prefix)
        bind(command, folder, '/results', readonly=False)
        tests = source/'tests'
        if embedded:
            tests = folder/'empty-tests'
            tests.mkdir()
        bind(command, tests, '/tests')
        bind(command, source/'run-tests.sh', '/probe/run-tests.sh')
        bind(command, tools, '/probe/pytest')
        if static_strace:
            bind(command, static_strace, '/usr/bin/strace')
        else:
            portable = tools/'portable-strace/root'
            bind(command, portable/'usr/bin/strace', '/usr/bin/strace')
            for lib in ('libunwind-ptrace.so.0','libunwind-x86_64.so.8','libunwind.so.8'):
                bind(command, (portable/'usr/lib/x86_64-linux-gnu'/lib).resolve(),
                     '/usr/lib/x86_64-linux-gnu/'+lib)
        command += ['-e', 'PYTHONPATH='+str(ROOT/'src')+':'+str(ROOT),
                    '-e', 'PYTHONDONTWRITEBYTECODE=1', '-e', 'AGENTTX_MOTIVATION_BWRAP=1',
                    '-e', 'AGENTTX_TB_PROBE_TASK='+record['task'],
                    '-e', 'AGENTTX_TB_EMBEDDED_TESTS='+str(int(embedded)),
                    '-e', 'AGENTTX_SKIP_ORPHAN_REAP=1', '-e', 'AGENTTX_TRACE_SPOOL_ROOT=/var/tmp',
                    '-e', 'AGENTTX_WORKER_PYTHON='+sys.executable,
                    '-e', 'TRY_BIND_READONLY='+':'.join(
                        value.split(',dst=',1)[1].split(',',1)[0] for value in command
                        if value.startswith('type=bind,') and value.endswith(',readonly')),
                    '--entrypoint', sys.executable, row['image_id'], '-u',
                    str(Path(__file__).resolve()), 'inside', '--output', '/results']
        save(folder/'command.json', command)
        with (folder/'container.stdout').open('w') as stdout, (folder/'container.stderr').open('w') as stderr:
            try:
                completed = subprocess.run(command, stdout=stdout, stderr=stderr, timeout=150)
                row['container_returncode'] = completed.returncode
            except subprocess.TimeoutExpired:
                row['container_returncode'] = 124
        inside_path = folder/'inside-result.json'
        if inside_path.exists():
            row['probe'] = json.loads(inside_path.read_text())
        else:
            row['error'] = 'container did not produce a diagnostic result; see container.stderr'
        probe_result = row.get('probe', {})
        row['status'] = ('runtime_or_transaction_error' if not probe_result.get('transaction_ok') else
                         'additional_services_required' if row['requires_additional_services'] else
                         'runtime_and_tests_probed' if probe_result.get('official_script_syntax_ok') and
                             probe_result.get('baseline_classification') in ('baseline_passed', 'baseline_failed') else
                         'test_environment_needs_work')
    except Exception as exc:
        status = ('runtime_or_transaction_error' if str(exc).startswith('Image user ') else 'preflight_error')
        row.update(status=status, error=type(exc).__name__+': '+str(exc))
        (folder/'error.txt').write_text(traceback.format_exc())
    finally:
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True, timeout=20)
    row.update(finished_at=now(), wall_s=time.monotonic()-start,
               full_upstream_verifier_validated=False, model_run_eligible=False)
    save(folder/'result.json', row)
    return row


def host(args):
    from experiments.workloads import terminal_bench_suite as tb
    from experiments.scripts.experiment_provenance import source_manifest
    from collections import Counter
    assert socket.gethostname() == 'r760-ssd'
    assert os.environ['DOCKER_HOST'] == 'unix:///run/agenttx-docker/docker.sock'
    assert shutil.disk_usage(DATA).free > 250*2**30
    evidence = json.loads(args.evidence.read_text())
    records = [r for r in evidence['checks'] if r['identity_verified']]
    if args.tasks:
        records = [r for r in records if r['task'] in args.tasks]
    assert records and len({r['task'] for r in records}) == len(records)
    args.output.mkdir(exist_ok=False)
    manifest = source_manifest(ROOT)
    save(args.output/'source-manifest.json', manifest)
    save(args.output/'registration.json', dict(registered_at=now(), host=socket.gethostname(),
        workers=args.workers, tasks=[r['task'] for r in records], model_calls=0,
        input_evidence_sha256=digest(args.evidence), source_sha256=manifest['sha256'],
        validator_script_sha256=digest(Path(__file__)),
        image_network='none', per_container_cpus=1, per_container_memory='2g',
        baseline_limit_s=45, container_limit_s=150, pytest_version='8.4.1',
        test_mode='Unmodified official test files; image Python; separate pinned pytest; maxfail=1.',
        limitation='Does not execute upstream installation/bootstrap script or launch compose sidecars; not full model launch eligibility.',
        tests_and_task_images_unchanged=True, gold_solution_used=False))
    tasks = tb.load_tasks(DATA/'cache')
    rows = []
    def progress():
        save(args.output/'progress.json', dict(updated_at=now(), coordinator_pid=os.getpid(),
            total=len(records), completed=len(rows), remaining=len(records)-len(rows),
            counts=dict(Counter(r['status'] for r in rows)),
            transaction_passed=sum(r.get('probe',{}).get('transaction_ok',False) for r in rows),
            baseline_classifications=dict(Counter(r.get('probe',{}).get('baseline_classification','not_run') for r in rows)),
            tasks=rows, model_calls=0))
    progress()
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(probe, record, args.output, args.tools, tasks, args.task_user_adapter, args.static_strace) for record in records]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            progress()
            print(json.dumps(dict(task=row['task'],status=row['status'],wall_s=row['wall_s'])), flush=True)
    progress()
    save(args.output/'execution.json', dict(finished_at=now(), attempted=len(rows), model_calls=0,
        results_immutable=True, tasks=rows))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['host','inside'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--evidence', type=Path, default=DATA/'validation/tb-readiness-recheck-20261018.json')
    parser.add_argument('--tools', type=Path, default=DATA/'validation/tb-validation-tools-20261018')
    parser.add_argument('--workers', type=int, default=8)
    parser.add_argument('--tasks', nargs='+')
    parser.add_argument('--task-user-adapter',action='store_true')
    parser.add_argument('--static-strace',type=Path)
    args = parser.parse_args()
    if args.command == 'inside': inside(args.output)
    else: host(args)


if __name__ == '__main__':
    main()
