#!/usr/bin/env python3
"""Exercise the real data-backed SWE transaction without calling a model."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import shlex

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT)]

def main():
    from agenttx.harness import CodingAgentHarness
    from experiments.workloads import swe_bench_suite as swe
    from experiments.scripts.run_swe_monitored_local import CACHE, DATA, save, now
    assert os.geteuid()==0 and os.environ['TMPDIR']=='/tmp'
    assert os.stat('/tmp').st_dev==os.stat(DATA/'tmp').st_dev
    workspace=Path(tempfile.mkdtemp(prefix='agenttx-swe-mount-preflight-',dir='/tmp'))
    session=Path(tempfile.mkdtemp(prefix='agenttx-swe-session-preflight-',dir='/tmp'))
    checks=[];harness=None;completed=False
    try:
        task=swe.load_tasks(CACHE)['psf__requests-1963']
        repo,instance=swe.ensure_repo(task,CACHE)
        swe.copy_repo(repo,workspace)
        python=swe.ensure_venv(task,sys.executable,CACHE,instance)
        python=swe.ensure_workspace_venv(workspace,python)
        subprocess.run(['git','init','--quiet'],cwd=workspace,check=True)
        harness=CodingAgentHarness(workspace,session,trace_backend='strace')
        probe=harness.call_tool('run_shell',{'cmd':python+' -c "import pytest, requests; print(\'dependencies_visible\')"'})
        assert probe.returncode==0,(probe.stderr,probe.stdout)
        checks.append('cached_task_dependencies_visible')
        socket_probe=harness.call_tool('run_shell',{'cmd':"docker image inspect --format '{{.Id}}' sha256:287f118ccaa9c0ba39e5f361550bd845d28933f34483861b8deaa4992f9d8d92"})
        assert socket_probe.returncode==0,(socket_probe.stderr,socket_probe.stdout)
        assert socket_probe.stdout.strip()=='sha256:287f118ccaa9c0ba39e5f361550bd845d28933f34483861b8deaa4992f9d8d92'
        checks.append('docker_socket_visible_inside_traced_transaction')
        write=harness.call_tool('write_file',{'path':'mount-preflight.txt','content':'speculative'})
        assert write.returncode==0,(write.stderr,write.stdout)
        assert not (workspace/'mount-preflight.txt').exists()
        check=harness.call_tool('read_file',{'path':'mount-preflight.txt'})
        assert check.stdout.strip()=='speculative'
        checks.append('speculative_write_visible_without_host_leak')
        harness.tx.rollback_from(write.step_id)
        assert not harness.tx.path_exists(workspace/'mount-preflight.txt')
        checks.append('rollback_removes_speculative_write')
        after_rollback=harness.call_tool('run_shell',{'cmd':'git rev-parse --is-inside-work-tree'})
        assert after_rollback.returncode==0 and after_rollback.stdout.strip()=='true',(after_rollback.stdout,after_rollback.stderr)
        checks.append('traced_commands_and_git_survive_rollback')
        runtime=swe.official_runtime_info(task.instance_id)
        instance={**instance,'_agenttx_eval_runtime':runtime}
        verifier=swe.agent_test_command(task,instance,python)
        base=harness.call_tool('run_shell',{'cmd':verifier})
        assert base.returncode==1,(base.returncode,base.stdout,base.stderr)
        assert '1 failed, 6 passed' in base.stdout,(base.stdout,base.stderr)
        checks.append('exact_agent_verifier_base_fails_only_issue_test')
        prior=next((DATA/'results/sweagent-swe-local-monitored-20261018-r2/cells/psf__requests-1963/clean_recovery').glob('trajectories/*/*/*.traj'))
        patch=json.loads(prior.read_text())['info']['submission']
        applied=harness.call_tool('run_shell',{'cmd':"printf '%s' "+shlex.quote(patch)+' | git apply -'})
        assert applied.returncode==0,(applied.stdout,applied.stderr)
        repaired=harness.call_tool('run_shell',{'cmd':verifier})
        assert repaired.returncode==0,(repaired.returncode,repaired.stdout,repaired.stderr)
        assert '7 passed' in repaired.stdout
        checks.append('exact_agent_verifier_prior_model_patch_passes_all_7')
        write=harness.call_tool('write_file',{'path':'mount-preflight.txt','content':'committed'})
        assert write.returncode==0
        harness.tx.commit()
        assert (workspace/'mount-preflight.txt').read_text().strip()=='committed'
        checks.append('commit_publishes_write')
        result=subprocess.run(['docker','run','--rm','--pull=never','--network=none',runtime['image_id'],
            '/opt/miniconda3/envs/testbed/bin/python','-c','import pytest; print("official_runtime_visible")'],capture_output=True,text=True,timeout=90)
        assert result.returncode==0,(result.stdout,result.stderr)
        checks.append('pinned_official_runtime_and_docker_socket_visible')
        after_commit=harness.call_tool('run_shell',{'cmd':'git rev-parse --is-inside-work-tree'})
        assert after_commit.returncode==0 and after_commit.stdout.strip()=='true'
        checks.append('traced_commands_and_git_survive_commit')
        save(DATA/'validation/swe-workspace-mount-preflight-20261018-r4.json',dict(
            checked_at=now(),ok=True,checks=checks,model_calls=0,workspace_alias=str(workspace),
            physical_tmp=str(DATA/'tmp'),readonly_infrastructure=os.environ['TRY_BIND_READONLY'],
            http_fixture_image=os.environ.get('AGENTTX_HTTPBIN_IMAGE'),gold_patch_used=False,
            prior_model_patch_used_only_for_infrastructure_preflight=True))
        print(json.dumps(dict(ok=True,checks=checks)),flush=True)
        completed=True
    finally:
        if harness:harness.close(destroy=completed)
        if completed:
            shutil.rmtree(workspace,ignore_errors=True)
            shutil.rmtree(session,ignore_errors=True)
        else:
            print(json.dumps(dict(ok=False,checks=checks,retained_workspace=str(workspace),retained_session=str(session),host_git_exists=(workspace/'.git').exists())),flush=True)
            for name in ('mount.log','worker.stderr.log'):
                p=session/name
                if p.exists():print(name,p.read_text(errors='replace')[-4500:],flush=True)

if __name__=='__main__':main()
