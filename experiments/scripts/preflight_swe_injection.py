#!/usr/bin/env python3
"""Inspect the real injection DAG without invoking a model or loading gold."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]


def main():
    from agenttx.harness import CodingAgentHarness
    from experiments.workloads import swe_bench_suite as swe
    from experiments.workloads.recovery_inject import dag_is_valid
    from experiments.scripts.run_swe_monitored_local import CACHE
    parser = argparse.ArgumentParser()
    parser.add_argument('--task', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    assert os.geteuid() == 0
    args.output.mkdir(exist_ok=False, parents=True)
    work = Path(tempfile.mkdtemp(prefix='agenttx-injection-work-', dir='/tmp'))
    session = Path(tempfile.mkdtemp(prefix='agenttx-injection-session-', dir='/tmp'))
    task = swe.load_tasks(CACHE)[args.task]
    repo, instance = swe.ensure_repo(task, CACHE)
    swe.copy_repo(repo, work)
    python = swe.ensure_workspace_venv(work, swe.ensure_venv(task, sys.executable, CACHE, instance))
    swe.prepare_workspace_runtime(work, instance, python)
    swe.seed_task_workspace(work, task, instance)
    subprocess.run(['git', 'init', '--quiet'], cwd=work, check=True)
    tmp = work / '.tmp'
    tmp.mkdir(exist_ok=True)
    for name in ('TMPDIR', 'TMP', 'TEMP'):
        os.environ[name] = str(tmp)
    harness = CodingAgentHarness(work, session, trace_backend='strace', ephemeral_roots=(tmp,))
    agent = type('InjectionProbe', (), {'harness': harness})()
    try:
        injection = swe.inject_task_trajectory(agent, task, instance, python)
        records = [dict(step_id=r.step_id, returncode=r.returncode,
                        stdout=r.stdout[-2500:], stderr=r.stderr[-2500:],
                        argv=[a if len(a)<1000 else a[:1000]+' [truncated]' for a in r.argv],
                        parents=list(r.parents)) for r in harness.tx.history]
        result = dict(task=args.task, valid=dag_is_valid(injection), injection=injection,
                      workdir=str(work), session_dir=str(session), records=records,
                      model_calls=0, gold_patch_used=False,
                      semisolate_sha256=hashlib.sha256((ROOT/'src/agenttx/semisolate.py').read_bytes()).hexdigest())
        if result['valid']:
            targets=harness.tx.rollback_exact(injection['oracle_invalid_steps'])
            restored=harness.call_tool('read_file',dict(path=task.faulty_relpath))
            result.update(rollback_targets=targets,
                          original_source_restored=restored.returncode==0 and restored.stdout==(work/task.faulty_relpath).read_text(),
                          independent_documents_retained=all(harness.tx.path_exists(work/p) for p in injection['docs_before']+injection['docs_after']),
                          derived_removed=not harness.tx.path_exists(work/'recovery_build/derived.txt'),
                          fault_write_succeeded=next(r for r in records if r['step_id']==injection['root_step'])['returncode']==0)
        (args.output / 'proof.json').write_text(json.dumps(result, indent=2) + '\n')
        print(json.dumps(dict(valid=result['valid'],output=str(args.output),model_calls=0)), flush=True)
    finally:
        harness.close(destroy=False)


if __name__ == '__main__':
    main()
