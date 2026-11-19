"""No-model native-image validation of the shared app/tmp mount layout."""
import datetime
import json
import os
from pathlib import Path
import subprocess
import sys

PROJECT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT / 'scripts'))
from patch_try_common_root import patch_source


def main():
    data = Path('/mnt/data/agenttx-runtime')
    audit = data / 'validation/tb-cross-filesystem-rename-20261028'
    source = data / 'results/tb-dual-native-full-20261027-r41-verifier-download-timeout/cells/path-tracing-reverse/sweagent/clean_recovery/clean_recovery'
    command = json.loads((source / 'container-command.json').read_text())
    command = command[:command.index('--entrypoint') + 3]
    frozen = Path('/home/bfq/agenttx-workspace/agenttx-executions/tb-dual-native-full-20261027-r41-verifier-download-timeout/project')
    patched = audit / 'try-common-root'
    patched.write_text(patch_source((frozen / 'third_party/try/try').read_text()))
    patched.chmod(0o755)
    folder = audit / ('shared-root-probe-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    folder.mkdir(parents=True)
    reference_mode = '--reference-input' in sys.argv
    reference_probe = []
    reference_mounts = []
    if reference_mode:
        sys.path[:0] = [str(PROJECT), str(PROJECT/'src')]
        from experiments.scripts.bench_tb_image_native import extract_reference_input, verifier_entrypoint_script
        os.environ['DOCKER_HOST'] = 'unix:///run/agenttx-docker/docker.sock'
        reference = folder/'immutable-mystery'
        extracted = extract_reference_input(command[-1], '/app/mystery', reference)
        (folder/'extracted-input.json').write_text(json.dumps(extracted,indent=2)+'\n')
        step = folder/'input-move.sh'
        step.write_text('python3 -c "import os;os.rename(\'/app/mystery\',\'/tmp/mystery\')"\n')
        legacy = folder/'legacy.sh'
        legacy.write_text(verifier_entrypoint_script(False,Path('/results/old-verifier'),'/diagnostic-step.sh'))
        fixed = folder/'fixed.sh'
        fixed.write_text(verifier_entrypoint_script(False,Path('/results/fixed-verifier'),'/diagnostic-step.sh',
                         reference_inputs=[('/diagnostic-original-mystery','/app/mystery')]))
        reference_mounts = [(reference,'/diagnostic-original-mystery'),(step,'/diagnostic-step.sh'),
                            (legacy,'/diagnostic-legacy.sh'),(fixed,'/diagnostic-fixed.sh')]
        reference_probe = [
            "first=h.tx.run_tool('legacy-first',['bash','/diagnostic-legacy.sh'],timeout_s=30)",
            "second=h.tx.run_tool('legacy-second',['bash','/diagnostic-legacy.sh'],timeout_s=30)",
            "assert first.returncode == 0 and second.returncode != 0, (first.stderr,second.stderr)",
            "fixed_first=h.tx.run_tool('fixed-first',['bash','/diagnostic-fixed.sh'],timeout_s=30)",
            "fixed_second=h.tx.run_tool('fixed-second',['bash','/diagnostic-fixed.sh'],timeout_s=30)",
            "assert fixed_first.returncode == fixed_second.returncode == 0, (fixed_first.stderr,fixed_second.stderr)",
        ]
    command[command.index('--network') + 1] = 'none'
    command[command.index('--cidfile') + 1] = str(folder / 'container.cid')
    replaced = 0
    for i, value in enumerate(command):
        if value.startswith('type=bind,src=' + str(source) + ',dst=/results'):
            command[i] = value.replace('src=' + str(source), 'src=' + str(folder), 1)
            replaced += 1
    assert replaced == 1
    assert not any('src=' + str(source) + ',dst=/results' in value for value in command)
    at = command.index('--entrypoint')
    command[at:at] = ['--mount', f'type=bind,src={patched},dst={frozen}/third_party/try/try,readonly',
                      '-e', 'TRY_COMMON_APP_TMP_OVERLAY=1']
    at = command.index('--entrypoint')
    extras = []
    for source_file, target in reference_mounts:
        extras += ['--mount',f'type=bind,src={source_file},dst={target},readonly']
    if extras:
        for i in range(len(command)-1):
            if command[i] == '-e' and command[i+1].startswith('TRY_BIND_READONLY='):
                command[i+1] += ':'+':'.join(target for _,target in reference_mounts)
        command[at:at] = extras
    inner = """import pathlib,os,json,hashlib
a=pathlib.Path('/app/.agenttx-rename-probe');b=pathlib.Path('/tmp/.agenttx-rename-probe')
a.write_text('probe')
out=dict(app_dev=os.stat('/app').st_dev,tmp_dev=os.stat('/tmp').st_dev)
os.rename(a,b)
out.update(rename_ok=True,content=b.read_text())
b.unlink()
m=pathlib.Path('/app/mystery');t=pathlib.Path('/tmp/mystery')
before=hashlib.sha256(m.read_bytes()).hexdigest()
os.rename(m,t);os.rename(t,m)
out['official_binary_roundtrip']=hashlib.sha256(m.read_bytes()).hexdigest()==before
print(json.dumps(out))
"""
    probe = '\n'.join([
        'import pathlib,json',
        'from experiments.scripts.bench_tb_image_native import prepare_runtime_tools',
        'from agenttx.harness import CodingAgentHarness',
        'from agenttx.policy import CommitPolicy',
        "prepare_runtime_tools(pathlib.Path('/results'))",
        "h=CodingAgentHarness(pathlib.Path('/app'),session_dir=pathlib.Path('/results/session'),trace_backend='strace',policy=CommitPolicy(pathlib.Path('/app'),allow_external_writes=True,ephemeral_roots=(pathlib.Path('/root'),pathlib.Path('/tmp'))))",
        "r=h.tx.run_tool('cross-filesystem-prerequisite',['/usr/bin/python3','-c'," + repr(inner) + '],timeout_s=30)',
        "assert r.returncode == 0, r.stderr",
        "seed=h.tx.run_tool('seed-rollback-probe',['bash','-c','printf original > /app/.agenttx-rollback-probe'],timeout_s=30)",
        "assert seed.returncode == 0, seed.stderr",
        "move=h.tx.run_tool('move-rollback-probe',['/usr/bin/python3','-c',\"import os;os.rename('/app/.agenttx-rollback-probe','/tmp/.agenttx-rollback-probe')\"],timeout_s=30)",
        "assert move.returncode == 0, move.stderr",
        "assert not pathlib.Path('/app/.agenttx-rollback-probe').exists(), 'private tool wrote coordinator workspace'",
        'h.tx.rollback(move.step_id)',
        "check=h.tx.run_tool('check-rollback-probe',['bash','-c','test \"$(cat /app/.agenttx-rollback-probe)\" = original && test ! -e /tmp/.agenttx-rollback-probe && printf rollback-ok'],timeout_s=30)",
        "assert check.returncode == 0 and check.stdout == 'rollback-ok', (check.returncode,check.stdout,check.stderr)",
        "with h.tx.fork() as fork:",
        "    forkcheck=fork.run_tool('fork-native-rename',['/usr/bin/python3','-c',\"import pathlib,os; a=pathlib.Path('/app/.agenttx-fork-probe');b=pathlib.Path('/tmp/.agenttx-fork-probe');a.write_text('fork');os.rename(a,b);assert b.read_text()=='fork'\"],timeout_s=30)",
        "    assert forkcheck.returncode == 0, forkcheck.stderr",
        '    fork.rollback(forkcheck.step_id)',
        "    fork_restored=fork.run_tool('fork-rollback-check',['bash','-c','test ! -e /tmp/.agenttx-fork-probe && test ! -e /app/.agenttx-fork-probe'],timeout_s=30)",
        "    assert fork_restored.returncode == 0, fork_restored.stderr",
        "assert not h.tx.path_exists(pathlib.Path('/tmp/.agenttx-fork-probe')), 'fork changed parent'",
    ] + reference_probe + [
        "print(json.dumps(dict(returncode=r.returncode,stdout=r.stdout,rollback_ok=True,fork_native_rename_ok=True,fork_parent_isolated=True,private_workspace_unchanged=True,reference_repeat_ok="+repr(reference_mode)+",stderr=r.stderr[-3000:])))",
    ])
    command += ['-c', probe]
    env = dict(os.environ, DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
    try:
        result = subprocess.run(command, env=env, text=True, capture_output=True, timeout=90)
    except subprocess.TimeoutExpired as exc:
        result = subprocess.CompletedProcess(command, 124, '', 'isolated probe exceeded 90 seconds')
    finally:
        cidfile = folder / 'container.cid'
        if cidfile.exists():
            cid = cidfile.read_text().strip()
            assert len(cid) == 64 and all(c in '0123456789abcdef' for c in cid)
            subprocess.run(['docker', 'rm', '-f', cid], env=env, text=True, capture_output=True, timeout=20)
    record = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                  returncode=result.returncode, stdout=result.stdout[-6000:], stderr=result.stderr[-6000:],
                  no_model_calls=True, active_workers_modified=False, host_forwarding_modified=False)
    (folder / 'result.json').write_text(json.dumps(record, indent=2) + '\n')
    print(folder)
    print(json.dumps(record))


if __name__ == '__main__':
    main()
