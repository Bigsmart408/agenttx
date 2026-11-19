"""No-model regression probe for terminal aliases in session backing storage."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'scripts'))
from patch_try_private_dev import patch_source


def main():
    data = Path('/mnt/data/agenttx-runtime')
    source = data / 'results/tb-dual-native-full-20261028-r54-swe-task-user-submit/cells/reshard-c4-data/codex/crash_no_rollback/crash_no_rollback'
    frozen = Path('/home/bfq/agenttx-workspace/agenttx-executions/tb-dual-native-full-20261028-r54-swe-task-user-submit/project')
    audit = data / 'validation/tb-private-dev-20261028'
    audit.mkdir(exist_ok=True)
    patched = audit / 'try-private-dev'
    original = (frozen / 'third_party/try/try').read_text()
    patched.write_text(patch_source(original))
    patched.chmod(0o755)
    env = dict(os.environ, DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    results = []
    for mode in ['original', 'fixed']:
        folder = audit / (mode + '-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
        folder.mkdir()
        command = json.loads((source / 'container-command.json').read_text())
        command = command[:command.index('--entrypoint') + 3]
        command[command.index('--network') + 1] = 'none'
        command[command.index('--cidfile') + 1] = str(folder / 'container.cid')
        count = 0
        for i, value in enumerate(command):
            if value.startswith('type=bind,src=' + str(source) + ',dst=/results'):
                command[i] = value.replace('src=' + str(source), 'src=' + str(folder), 1)
                count += 1
        assert count == 1
        if mode == 'fixed':
            at = command.index('--entrypoint')
            command[at:at] = ['--mount', f'type=bind,src={patched},dst={frozen}/third_party/try/try,readonly']
        inner = '''import os,json,pathlib
p=pathlib.Path('/results/session/temproot/dev/ptmx')
out={'backing_terminal_alias':p.is_symlink(),'live_ptmx':pathlib.Path('/dev/ptmx').exists(),'shm':pathlib.Path('/dev/shm').is_dir()}
pathlib.Path('/app/.private-dev-rollback').write_text('first')
print(json.dumps(out))
'''
        if mode == 'fixed':
            inner += '''import ast,hashlib,time
tree=ast.parse(pathlib.Path('/probe/tests/test_outputs.py').read_text())
node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='take_filesystem_snapshot')
space={'os':os,'hashlib':hashlib}
exec(compile(ast.Module(body=[node],type_ignores=[]),'<original-official-snapshot>','exec'),space)
started=time.monotonic()
snapshot=space['take_filesystem_snapshot']()
print(json.dumps({'official_snapshot_completed':True,'files':len(snapshot),'seconds':time.monotonic()-started}))
'''
        probe = '\n'.join([
            'import pathlib,json',
            'from experiments.scripts.bench_tb_image_native import prepare_runtime_tools',
            'from agenttx.harness import CodingAgentHarness',
            'from agenttx.policy import CommitPolicy',
            "prepare_runtime_tools(pathlib.Path('/results'))",
            "h=CodingAgentHarness(pathlib.Path('/app'),session_dir=pathlib.Path('/results/session'),trace_backend='strace',policy=CommitPolicy(pathlib.Path('/app'),allow_external_writes=True,ephemeral_roots=(pathlib.Path('/root'),pathlib.Path('/tmp'))))",
            "r=h.tx.run_tool('private-dev-first',['/usr/bin/python3','-c'," + repr(inner) + '],timeout_s=60)',
            'assert r.returncode == 0, (r.returncode,r.stderr)',
            "lines=r.stdout.splitlines();v=json.loads(lines[0]);assert v['live_ptmx'],v",
            "snapshot=json.loads(lines[-1]) if " + repr(mode == 'fixed') + " else None",
            "assert snapshot is None or snapshot['official_snapshot_completed'],snapshot",
            "assert v['backing_terminal_alias'] == " + repr(mode == 'original') + ',v',
            'h.tx.rollback(r.step_id)',
            "check=h.tx.run_tool('rollback-check',['bash','-c','test ! -e /app/.private-dev-rollback && test -e /dev/ptmx && echo rollback-ok'],timeout_s=30)",
            "assert check.returncode == 0 and 'rollback-ok' in check.stdout,(check.stderr,check.stdout)",
            'with h.tx.fork() as fork:',
            "    f=fork.run_tool('fork-device-check',['bash','-c','test -e /dev/ptmx && echo fork-ok'],timeout_s=30)",
            "    assert f.returncode == 0 and 'fork-ok' in f.stdout,(f.stderr,f.stdout)",
            "print(json.dumps(dict(mode=" + repr(mode) + ',observed=v,snapshot=snapshot,rollback_ok=True,fork_ok=True)))',
        ])
        command += ['-c', probe]
        try:
            r = subprocess.run(command, env=env, text=True, capture_output=True, timeout=90)
            record = {'mode':mode,'returncode':r.returncode,'stdout':r.stdout[-5000:],'stderr':r.stderr[-5000:]}
        except subprocess.TimeoutExpired:
            record = {'mode':mode,'returncode':124,'error':'isolated probe exceeded 90 seconds'}
        finally:
            cidfile = folder / 'container.cid'
            if cidfile.exists():
                cid = cidfile.read_text().strip()
                assert len(cid) == 64 and all(c in '0123456789abcdef' for c in cid)
                subprocess.run(['docker','rm','-f',cid],env=env,capture_output=True,timeout=20)
        (folder / 'result.json').write_text(json.dumps(record,indent=2)+'\n')
        results.append(record)
        print(json.dumps(record),flush=True)
    summary = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),no_model_calls=True,
                   active_workers_modified=False,host_forwarding_modified=False,results=results)
    (audit / 'latest-result.json').write_text(json.dumps(summary,indent=2)+'\n')
    return int(any(r['returncode'] for r in results))


if __name__ == '__main__':
    raise SystemExit(main())
