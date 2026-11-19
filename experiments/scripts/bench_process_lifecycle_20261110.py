#!/usr/bin/env python3
"""Three-arm fixed trajectories with observed process-state lifetimes."""
from __future__ import annotations

import argparse
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
import weakref

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / 'src'), str(ROOT)]
from experiments.scripts.bench_process_state_20261110 import DOCKER, rpc, save, stop_service

SCRIPT = 'experiments/scripts/bench_process_lifecycle_20261110.py'
FAMILIES = ('request_scope', 'read_through', 'lru', 'ttl', 'job_exit', 'unused_cache', 'persistent')
ARMS = ('retain_all', 'clear_all', 'clean')
HORIZON = 16


def now():
    return datetime.now(timezone.utc).isoformat()


class Payload:
    def __init__(self, value):
        self.value = value
        self.storage = (value.encode() * (4096 // len(value.encode()) + 1))[:4096]


class Observation:
    """Weak references observe lifetime without keeping payloads alive."""
    def __init__(self):
        self.refs = []
        self.released = []
        self.phase = 'prefix'
        self.t0 = time.monotonic()

    def new(self, value):
        obj = Payload(value)
        if value.startswith('faulty-'):
            index = len(self.refs)
            self.refs.append(weakref.ref(obj))
            weakref.finalize(obj, self.release, index)
        return obj

    def release(self, index):
        self.released.append(dict(index=index, phase=self.phase, elapsed_s=time.monotonic() - self.t0))

    def snapshot(self):
        return dict(created=len(self.refs), resident=sum(ref() is not None for ref in self.refs),
                    released=len(self.released), releases=list(self.released))


def serve(args):
    observation = Observation()
    cache, memo = OrderedDict(), []
    deadline, calls, warm_items = None, 0, 0

    def warm(data, count):
        nonlocal memo, warm_items
        memo = [hashlib.sha256((data + str(i)).encode()).hexdigest() for i in range(count)]
        warm_items += count

    def snapshot(value=None, exiting=False):
        rss = next(int(line.split()[1]) for line in Path('/proc/self/status').read_text().splitlines()
                   if line.startswith('VmRSS:'))
        return dict(pid=os.getpid(), value=value, memo_count=len(memo),
            memo_sha256=hashlib.sha256(json.dumps(memo).encode()).hexdigest(),
            warm_items=warm_items, obsolete=observation.snapshot(), rss_kib=rss,
            calls=calls, exiting=exiting, elapsed_s=time.monotonic() - observation.t0)

    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen()
        print(json.dumps(dict(port=listener.getsockname()[1], pid=os.getpid())), flush=True)
        while True:
            conn, _ = listener.accept()
            exiting = False
            with conn, conn.makefile('rb') as stream:
                req = json.loads(stream.readline())
                observation.phase = req.get('phase', 'inspect')
                value = None
                if req['op'] == 'warm':
                    warm(req['valid'], req['items'])
                elif req['op'] == 'prime':
                    if args.family == 'request_scope':
                        temporary = [observation.new(req['data']) for _ in range(req['items'])]
                        del temporary
                    else:
                        for i in range(req['items']):
                            cache[f'prefix-{i}'] = observation.new(req['data'])
                        deadline = time.monotonic() + args.ttl
                elif req['op'] == 'serve':
                    calls += 1
                    if req['warm_required'] and not memo:
                        warm(req['valid'], req['items'])
                    if args.family == 'read_through':
                        cache.clear()
                        for i in range(req['items']):
                            cache[f'refreshed-{i}'] = observation.new(req['data'])
                    elif args.family == 'lru':
                        for j in range(8):
                            cache[f'new-{calls}-{j}'] = observation.new(req['data'])
                            if len(cache) > req['items']:
                                cache.popitem(last=False)
                    elif args.family == 'ttl' and deadline is not None and time.monotonic() >= deadline:
                        cache.clear()
                    value = next(iter(cache.values())).value if args.family == 'persistent' and cache else req['data']
                    exiting = args.family == 'job_exit' and calls >= args.job_calls
                elif req['op'] != 'inspect':
                    raise ValueError(req['op'])
                conn.sendall(json.dumps(snapshot(value, exiting)).encode() + b'\n')
            if exiting:
                return 0


def start_service(family, seed, log):
    stream = log.open('w')
    process = subprocess.Popen([sys.executable, '-u', str(ROOT / SCRIPT), 'service',
        '--family', family, '--ttl', str(.1 * (seed + 1)), '--job-calls', str(4 + seed)],
        stdout=subprocess.PIPE, stderr=stream, text=True, start_new_session=True)
    try:
        ready = json.loads(process.stdout.readline())
        assert ready['pid'] == process.pid
        return dict(process=process, log=stream, **ready)
    except BaseException:
        stop_service(dict(process=process, log=stream))
        raise


CLIENT = '''import json,socket,sys
from pathlib import Path
ports=json.loads(sys.argv[1]); req=json.loads(sys.argv[2])
req['data']=Path('fault.txt').read_text();req['valid']=Path('valid.txt').read_text()
responses=[]
for i,port in enumerate(ports):
 payload=dict(req);payload['warm_required']=(i==1 or req['family']!='job_exit')
 if i==1 and req['op']=='prime': payload['data']=req['valid']
 with socket.create_connection(('127.0.0.1',port),timeout=5) as s:
  s.sendall(json.dumps(payload).encode()+b'\\n')
  with s.makefile('rb') as f: responses.append(json.loads(f.readline()))
print(json.dumps(responses))
'''


def trial(output, family, seed, repeat, arm):
    from agenttx.runtime import AgentTX
    output.mkdir(parents=True)
    workspace = output / 'workspace'
    workspace.mkdir()
    good, bad = f'correct-{seed}\n', f'faulty-{seed}\n'
    valid = f'independent-{seed}\n' * 256
    (workspace / 'fault.txt').write_text(good)
    (workspace / 'valid.txt').write_text(valid)
    n = 32 * (seed + 1)
    tx, services, instances = None, [], []
    series, lifecycle = [], []
    started = time.perf_counter()
    row = dict(family=family, seed=seed, repeat=repeat, arm=arm, horizon=HORIZON,
               items=n, model_calls=0, model_api_requests=0, started_at=now())
    try:
        services = [start_service(family, seed, output / 'service-a-0.log'),
                    start_service('unused_cache', seed, output / 'service-b-0.log')]
        instances.extend(services)
        tx = AgentTX.begin(workspace, output / 'session', trace_backend='strace')
        tx.conversation.seed('Execute the fixed trajectory.', 'Keep independent progress.')

        def tool(name, argv):
            record = tx.run_tool(name, argv, timeout_s=15)
            if record.returncode:
                raise RuntimeError(f'{name}: {record.stderr}')
            call = f'call-{record.step_id}'
            tx.conversation.append_turn([dict(role='assistant', content=None,
                tool_calls=[dict(id=call,type='function',function=dict(name=name,arguments=json.dumps(dict(argv=argv))))]),
                dict(role='tool',tool_call_id=call,content=record.stdout)],
                step_ids=[record.step_id],tool_names=[name],kind='effect')
            tx._persist()
            return record

        def operate(op, phase):
            record = tool(phase, [sys.executable,'-c',CLIENT,
                json.dumps([s['port'] for s in services]),
                json.dumps(dict(op=op, phase=phase, items=n, family=family))])
            return json.loads(record.stdout)

        # Warm exactly the valid state the clean arm will use later.
        for i, s in enumerate(services):
            if i == 1 or family != 'job_exit':
                rpc(s['port'],dict(op='warm',valid=valid,items=n,phase='prefix-warm'))
        root = tool('write-input', [sys.executable,'-c',
            'from pathlib import Path; Path("fault.txt").write_text(' + repr(good if arm=='clean' else bad) + ')'])
        operate('prime','consume-input')
        derived = tool('derive-output', [sys.executable,'-c',
            'from pathlib import Path; Path("derived.txt").write_text(Path("fault.txt").read_text())'])
        independent = tool('independent-output', [sys.executable,'-c',
            'from pathlib import Path; Path("independent.txt").write_text(Path("valid.txt").read_text())'])
        cut = [rpc(s['port'],dict(op='inspect',phase='cut')) for s in services]
        save(output/'at-cut.json',cut)
        selected = [] if arm=='clean' else tx.rollback_causal(root.step_id)
        active = set(tx.conversation.active_step_ids())

        def view(name):
            p=tx._overlay_entry(workspace/name)
            return p.read_text() if p is not None and p.is_file() else None

        checks=dict(input_correct=view('fault.txt')==good,
            independent_file_retained=view('independent.txt')==valid,
            independent_context_retained=independent.step_id in active,
            faulty_context_withdrawn=(root.step_id in active)==(arm=='clean'),
            dependent_context_withdrawn=(derived.step_id in active)==(arm=='clean'),
            dependent_file_matches=view('derived.txt')==(good if arm=='clean' else None))
        save(output/'context-at-recovery.json',tx.conversation.to_dict())
        past_warm = 0
        if arm=='clear_all':
            past_warm=sum(x['warm_items'] for x in cut)
            for s in services:
                stop_service(s)
            services=[start_service(family,seed,output/'service-a-cleared.log'),
                      start_service('unused_cache',seed,output/'service-b-cleared.log')]
            instances.extend(services)
        after=[rpc(s['port'],dict(op='inspect',phase='after-policy')) for s in services]
        save(output/'after-policy.json',after)
        checks['process_policy_correct']=all((a['pid']==b['pid'])==(arm!='clear_all') for a,b in zip(cut,after))
        # The normal suffix regenerates the file output and uses the services.
        # It neither names obsolete objects nor issues GC/eviction/cleanup RPCs.
        tool('regenerate-output',[sys.executable,'-c',
            'from pathlib import Path; Path("derived.txt").write_text(Path("fault.txt").read_text())'])
        exited_created, exited_released, exited_warm = 0,0,0
        for step in range(1,HORIZON+1):
            response=operate('serve',f'continue-{step:02d}')
            snapshot=dict(step=step, responses=response, elapsed_s=time.perf_counter()-started,
                          exited_created=exited_created,exited_released=exited_released)
            series.append(snapshot)
            if response[0]['exiting']:
                old=services[0]
                old['process'].wait(timeout=5)
                assert old['process'].returncode==0
                old['process'].stdout.close();old['log'].close()
                exited_created+=response[0]['obsolete']['created']
                exited_released+=response[0]['obsolete']['resident']
                exited_warm+=response[0]['warm_items']
                lifecycle.append(dict(step=step,pid=old['pid'],exit_code=0,
                    released_units=response[0]['obsolete']['resident'],reason='normal-job-completion'))
                replacement=start_service(family,seed,output/f'service-a-after-job-{step:02d}.log')
                services[0]=replacement;instances.append(replacement)
                # Count reclamation at actual process exit, after its last reply.
                snapshot.update(exited_created=exited_created,exited_released=exited_released,
                                natural_exit_after_response=True)
            save(output/'continuation.json',series)
            save(output/'lifecycle.json',lifecycle)
        final=[rpc(s['port'],dict(op='inspect',phase='final-observation')) for s in services]
        save(output/'final-processes.json',final)
        wrong=sum(s['responses'][0]['value']!=good for s in series)
        created=cut[0]['obsolete']['created']
        initially_resident=cut[0]['obsolete']['resident']
        final_resident=final[0]['obsolete']['resident']
        final_released=final[0]['obsolete']['released']+exited_released
        # Forced process clearing is recorded separately from natural release.
        naturally_released=(created-final_resident) if arm=='retain_all' else 0
        if arm=='retain_all':
            assert naturally_released==final_released
        memo_expected=(1 if family=='job_exit' else 2)*n
        final_memo=sum(x['memo_count'] for x in final)
        total_warm=past_warm+exited_warm+sum(x['warm_items'] for x in final)
        checks.update(final_file_correct=view('derived.txt')==good,
                      final_valid_memory_complete=final_memo==memo_expected)
        row.update(execution_passed=all(checks.values()),checks=checks,rollback_ids=selected,
            initial_pids=[x['pid'] for x in cut], after_policy_pids=[x['pid'] for x in after],
            final_pids=[x['pid'] for x in final], wrong_queries=wrong,
            all_queries_correct=wrong==0, invalid_units_created=created,
            invalid_units_at_cut=initially_resident, invalid_units_at_end=final_resident,
            naturally_released_total=naturally_released,
            released_before_recovery=created-initially_resident,
            naturally_released_after_recovery=initially_resident-final_resident if arm=='retain_all' else 0,
            natural_release_fraction=naturally_released/created if created and arm=='retain_all' else None,
            post_recovery_release_fraction=(initially_resident-final_resident)/initially_resident
                if initially_resident and arm=='retain_all' else None,
            forced_released_units=initially_resident if arm=='clear_all' else 0,
            valid_items_expected=memo_expected,
            valid_items_immediately_retained=sum(x['memo_count'] for x in after),
            valid_items_final=final_memo, warm_items_total=total_warm,
            normal_job_exits=len(lifecycle), final_rss_kib=sum(x['rss_kib'] for x in final))
        tx.dump_ledger(output/'ledger.json')
    except Exception as exc:
        import traceback
        row.update(execution_passed=False,error=f'{type(exc).__name__}: {exc}')
        (output/'error.txt').write_text(traceback.format_exc())
    finally:
        for s in services:
            stop_service(s)
        if tx is not None:
            tx.close(destroy=False)
        row.update(finished_at=now(),wall_s=time.perf_counter()-started)
        save(output/'result.json',row)
    return row


def worker(args):
    r=args.cohort
    try:
        triples=[(f,s,k) for k in range(3) for f in FAMILIES for s in range(10)]
        def triple(key):
            family,seed,repeat=key
            offset=(seed+repeat)%3
            order=ARMS[offset:]+ARMS[:offset]
            rows=[]
            for arm in order:
                out=r/'trials'/family/f'seed-{seed:02d}'/f'repeat-{repeat:02d}'/arm
                row=trial(out,family,seed,repeat,arm)
                print(json.dumps({k:row.get(k) for k in ('family','seed','repeat','arm',
                    'execution_passed','wrong_queries','invalid_units_at_end','error')}),flush=True)
                if not row['execution_passed']:
                    raise RuntimeError(str(out)+': '+str(row.get('error',row.get('checks'))))
                rows.append(row)
            return rows
        rows=[]
        with ThreadPoolExecutor(max_workers=4) as pool:
            for group in pool.map(triple,triples):
                rows.extend(group)
                save(r/'progress.json',dict(at=now(),completed=len(rows),expected=630,
                    model_calls=0,model_api_requests=0))
        save(r/'summary.json',dict(at=now(),completed=len(rows),expected=630,
            execution_passed=sum(x['execution_passed'] for x in rows),
            model_calls=0,model_api_requests=0))
    finally:
        for directory,dirs,files in os.walk(r,followlinks=False):
            for p in [Path(directory),*(Path(directory)/n for n in dirs+files)]:
                try:os.chown(p,args.result_uid,args.result_gid,follow_symlinks=False)
                except FileNotFoundError:pass


def prepare(args):
    from experiments.scripts.experiment_provenance import source_manifest
    r=args.cohort.resolve();r.mkdir(mode=0o700)
    source=ROOT.parent/'agenttx-executions'/r.name/'project';source.mkdir(parents=True)
    manifest=source_manifest(ROOT)
    for item in manifest['files']:
        dest=source/item['path'];dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(ROOT/item['path'],dest)
        assert hashlib.sha256(dest.read_bytes()).hexdigest()==item['sha256']
    reference=json.loads((ROOT.parent/'agenttx-experiment-data/results/rq1-strategy-only-6000-20261110-r1/registration.json').read_text())
    save(r/'source-manifest.json',manifest)
    save(r/'registration.json',dict(registered_at=now(),cohort=str(r),frozen_source=str(source),
        source_sha256=manifest['sha256'],image_id=reference['image_id'],python=reference['python'],
        python_mounts=reference['python_mounts'],uid=os.getuid(),gid=os.getgid(),families=FAMILIES,
        arms=ARMS,seeds=10,repetitions=3,matched_triples=210,expected_trials=630,
        continuation_calls=HORIZON,parallel_triples=4,model_calls=0,model_api_requests=0,
        trajectory='fixed scripted; supplied fault root; no agents',network='none',
        clean='same workload with correct input throughout; no fault, rollback, or process reset',
        release_unit='one 4-KiB payload object from the faulty consume; observed through weak references',
        release_denominator='created invalid units; cut-resident invalid units separately',
        natural_release='normal scope exit, refresh, LRU eviction, TTL-on-access, or job exit; excludes reset and teardown',
        lru_capacity='32*(seed+1); eight new keys per continuation call',
        ttl_s='0.1*(seed+1); expiry checked on ordinary requests',job_requests='4+seed',
        process_scope='two application processes outside disposable tool worker'))
    print(str(r),flush=True)


def run(args):
    r=args.cohort.resolve();reg=json.loads((r/'registration.json').read_text());source=Path(reg['frozen_source'])
    for item in json.loads((r/'source-manifest.json').read_text())['files']:
        assert hashlib.sha256((source/item['path']).read_bytes()).hexdigest()==item['sha256']
    mounts=sorted({source,*map(Path,reg['python_mounts'])})
    cmd=DOCKER+['run','--rm','--pull','never','--privileged','--network','none','--name',r.name,
        '--tmpfs','/root:exec','--tmpfs','/tmp:exec','--tmpfs','/sys','--tmpfs','/run','--workdir',str(source)]
    for p in mounts:cmd+=['--mount',f'type=bind,src={p},dst={p},readonly']
    cmd+=['--mount',f'type=bind,src={r},dst=/results']
    for k,v in dict(PYTHONPATH=f'{source}/src:{source}',PYTHONDONTWRITEBYTECODE='1',
        AGENTTX_MOTIVATION_BWRAP='1',AGENTTX_NO_PROXY='1',AGENTTX_SKIP_ORPHAN_REAP='1',
        AGENTTX_WORKER_PYTHON=reg['python'],AGENTTX_TRACE_SPOOL_ROOT='/var/tmp',
        TRY_BIND_READONLY=':'.join(map(str,mounts))).items():cmd+=['--env',f'{k}={v}']
    cmd+=['--entrypoint',reg['python'],reg['image_id'],'-u',str(source/SCRIPT),'worker',
          '--cohort','/results','--result-uid',str(reg['uid']),'--result-gid',str(reg['gid'])]
    return subprocess.call(cmd)


def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='action',required=True)
    for name in ('prepare','run','worker'):
        q=sub.add_parser(name);q.add_argument('--cohort',type=Path,required=True)
        if name=='worker':
            q.add_argument('--result-uid',type=int,required=True);q.add_argument('--result-gid',type=int,required=True)
    q=sub.add_parser('service');q.add_argument('--family',choices=FAMILIES,required=True)
    q.add_argument('--ttl',type=float,default=.1);q.add_argument('--job-calls',type=int,default=4)
    args=p.parse_args()
    if args.action=='service':return serve(args)
    if args.action=='prepare':prepare(args)
    elif args.action=='worker':worker(args)
    else:return run(args)
    return 0


if __name__=='__main__':raise SystemExit(main())
