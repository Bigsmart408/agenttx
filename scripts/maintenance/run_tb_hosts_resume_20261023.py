#!/usr/bin/env python3
"""Resume immutable TB results; repair attempts follow network failures, never best-of."""
import collections,copy,datetime,fcntl,hashlib,json,os,re,shutil,signal,subprocess,sys,time,tomllib,urllib.request
from pathlib import Path
sys.path[:0]=['/home/bfq/agenttx','/home/bfq/agenttx/src','/home/bfq/agenttx-maintenance']
import run_tb_retry_queue_20261022 as old
from experiments.scripts.run_tb_dual_local import cell_command,infrastructure_issue,effects,experiment_configuration,CELL_TIMEOUT_S
from experiments.scripts.experiment_provenance import source_manifest
D=Path('/mnt/data/agenttx-runtime');NAME='tb-dual-native-full-20261023-r19-hosts-resume';ROOT=D/'results'/NAME
PREVIOUS=D/'results/tb-dual-native-full-20261023-r18-network-resume'
AUDIT=D/'validation/tb-hosts-resume-20261023'
FROZEN=Path('/home/bfq/agenttx-executions')/NAME/'project'
WINDOW='2026-10-22T20:56:00+00:00'
PROFILE=Path('/home/bfq/.config/agenttx/experiment-20261017')
now=old.now;save=old.save;sha=old.sha;state=old.state;key=old.key
TERMINAL={'completed','infrastructure_error'}

def connection_evidence(row):
    if row['state'] not in TERMINAL:return None
    path=Path(row['result_path']);result=json.loads(path.read_text())
    if row['state']=='completed' and result.get('success'):return None
    # Scope evidence to model errors, never the task's verifier/network tests.
    texts=[]
    for name in ('accounting-summary.json','failure-diagnostics.json'):
        p=path.parent/name
        if p.is_file():
            d=json.loads(p.read_text());texts.append(json.dumps(d.get('model_summaries',{})));texts.append(json.dumps(d.get('model_error_lines',[])))
    for name in ('model-result.json','replay-result.json'):
        p=path.parent/name
        if p.is_file():
            d=json.loads(p.read_text());texts.append(str(d.get('stderr','')));texts.append(json.dumps([x for x in d.get('events',[]) if isinstance(x,dict) and x.get('type') in {'error','turn.failed'}]))
    diag=path.parent/'failure-diagnostics.json'
    if diag.exists():
        diagnostic=json.loads(diag.read_text());verifier=diagnostic.get('verifier.stdout.log','')
    else:
        verifier_path=path.parent/'verifier.stdout.log';verifier=verifier_path.read_text(errors='replace') if verifier_path.exists() else ''
    if "Failed to resolve 'localhost'" in verifier:
        return dict(classification='transaction_hosts_baseline_missing_localhost',source_result=str(path),source_finished_at=row.get('network_failure_evidence',{}).get('source_finished_at',row.get('finished_at','')),failed_attempt_finished_at=row.get('finished_at',''))
    text='\n'.join(texts)
    match=re.search(r'Connection refused|APIConnectionError|Connection error\.|error sending request for url|stream disconnected before completion|connection failed',text,re.I)
    if match:return dict(classification='explicit_model_connection_failure',matched=match.group(0),source_result=str(path),source_finished_at=row.get('finished_at',''))
    # Retention did not preserve every original network message. Keep this
    # conservative time-window inference separate from explicit evidence.
    if row['state']=='infrastructure_error' and row.get('finished_at','')>=WINDOW and not result.get('api_usage',{}).get('complete'):
        return dict(classification='incomplete_usage_during_confirmed_connection_incident',source_result=str(path),source_finished_at=row.get('finished_at',''),inferred=True)
    return None

def add_network_retries(cells,retries):
    latest={key(r):r for r in cells}
    for r in retries:latest[key(r)]=r
    added=[]
    for k,prior in list(latest.items()):
        evidence=connection_evidence(prior)
        if not evidence:continue
        # At most one fresh repair attempt per cell in this repair cohort.
        history=[r for r in retries if key(r)==k]
        if any(r.get('network_repair_cohort')==NAME for r in history):continue
        attempt=max([0]+[r.get('attempt',1) for r in history])+1
        first=next(r for r in cells if key(r)==k)
        row=dict(task=k[0],harness=k[1],policy=k[2],expected_context=prior['expected_context'],
            state='pending',attempt=attempt,network_repair_cohort=NAME,
            retry_reason='user_authorized_resume_from_connection_refusal',network_failure_evidence=evidence,
            original_state=first['state'],original_result_path=first['result_path'],original_result_sha256=sha(first['result_path']),
            previous_attempt_path=prior['result_path'],previous_attempt_sha256=sha(prior['result_path']))
        retries.append(row);added.append(row)
    # Existing, unstarted normal retries of connection-affected originals are
    # marked for priority; do not create a duplicate attempt beside them.
    for row in retries:
        if row['state']=='pending' and 'network_repair_cohort' not in row:
            evidence=connection_evidence(next(r for r in cells if key(r)==key(row)))
            if evidence:
                row.update(network_repair_cohort=NAME,network_failure_evidence=evidence,
                    retry_reason='user_authorized_resume_from_connection_refusal')
    return added

def select(cells,retries,active):
    pending=[row for row in retries if row['state']=='pending' and row.get('network_repair_cohort')]
    if pending:return min(pending,key=lambda r:r.get('network_failure_evidence',{}).get('source_finished_at','')),True
    return old.select(cells,retries,active)

def result_path(row,retry):
    category=('network-retry-cells' if row.get('network_repair_cohort') else 'retry-cells') if retry else 'cells'
    return ROOT/category/row['task']/row['harness']/row['policy']/row['policy']/'host-result.json'

def health():
    try:
        config=tomllib.loads((PROFILE/'config.toml').read_text());base=config['model_providers'][config['model_provider']]['base_url'].rstrip('/')
        auth=json.loads((PROFILE/'auth.json').read_text())['OPENAI_API_KEY']
        req=urllib.request.Request(base+'/models',headers={'Authorization':'Bearer '+auth})
        with urllib.request.urlopen(req,timeout=8) as response:assert response.status==200;response.read()
        return dict(checked_at=now(),healthy=True,http_status=200)
    except Exception as e:return dict(checked_at=now(),healthy=False,error_type=type(e).__name__)

def report(cells,retries):
    data=old.retry_report(cells,retries)
    data['accounting_definition']='Latest attempted outcome; preserve every original/retry/one incident repair attempt and sum all known main+replay spend. Any incomplete attempt excludes the cell from complete-cost pairs. No best-attempt selection.'
    latest={key(r):r for r in cells}
    for r in retries:
        if r['state'] in TERMINAL:latest[key(r)]=r
    data['latest_outcome_counts']=dict(collections.Counter(r['state'] for r in latest.values()))
    repaired=[r for r in retries if r.get('network_repair_cohort')==NAME]
    data['network_repair_counts']=dict(collections.Counter(r['state'] for r in repaired))
    data['network_repair_successful']=sum(r['state']=='completed' and json.loads(Path(r['result_path']).read_text()).get('success',False) for r in repaired)
    return data

def prepare_source(reg):
    prior=Path(reg['frozen_source']);manifest=json.loads((PREVIOUS/'source-manifest.json').read_text());FROZEN.mkdir(parents=True)
    for entry in manifest['files']:
        src=prior/entry['path'];assert sha(src)==entry['sha256'];dst=FROZEN/entry['path'];dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    for relative in ('experiments/scripts/bench_tb_image_native.py','experiments/scripts/tb_task_services.py'):
        shutil.copy2(Path('/home/bfq/agenttx')/relative,FROZEN/relative)
    return source_manifest(FROZEN)

def main():
    assert '--start' in sys.argv
    AUDIT.mkdir(parents=True,exist_ok=True)
    own_lock=(AUDIT/'coordinator.lock').open('a');fcntl.flock(own_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    assert not ROOT.exists(),'Explicit continuation required; never overwrite existing results'
    pointer=json.loads((D/'validation/active-tb-dual-native.json').read_text());assert Path(pointer['root'])==PREVIOUS
    old_pid=pointer['coordinator_pid'];assert state(old_pid)=='T','Previous dispatcher must be stopped, never dual-dispatch'
    previous=json.loads((PREVIOUS/'progress.json').read_text());save(AUDIT/'previous-progress.json',previous)
    ancestors=[];ancestor_root=PREVIOUS
    while (ancestor_root/'progress.json').exists():
        ap=json.loads((ancestor_root/'progress.json').read_text());ar=json.loads((ancestor_root/'registration.json').read_text());apid=ap['coordinator_pid']
        if state(apid)!='T':break
        ancestors.append(dict(root=str(ancestor_root),pid=apid,progress=ap));prior=ar.get('previous_cohort')
        if not prior:break
        ancestor_root=D/'results'/prior
    assert ancestors and ancestors[0]['pid']==old_pid
    save(AUDIT/'stopped-ancestors.json',ancestors)
    cells=copy.deepcopy(previous['tasks']);retries=copy.deepcopy(previous.get('retry_tasks',[]))
    reg=json.loads((PREVIOUS/'registration.json').read_text());assert reg['workers_total']==8 and reg['min_free_disk_gib']==0
    assert health()['healthy'],'Configured API is not healthy'
    proof=json.loads((D/'validation/tb-api-connection-repair-20261023/isolation-verification.json').read_text())
    assert proof['host_nat_unchanged'] and proof['forwarding_8080_unmodified'] and proof['private_authenticated_status']==200
    for harness in ('codex','sweagent'):
        hp=json.loads((D/'validation/tb-api-connection-repair-20261023'/('hosts-baseline-'+harness+'-verification.json')).read_text())
        assert hp['network_baseline']['localhost_resolution'] and hp['adapter_import'] and hp['no_model_calls']
    manifest=prepare_source(reg);ROOT.mkdir()
    protected={r['result_path']:sha(r['result_path']) for r in cells+retries if r['state'] in TERMINAL}
    save(AUDIT/'protected-results.json',protected)
    registration=dict(reg,registered_at=now(),cohort=NAME,frozen_source=str(FROZEN),source_sha256=manifest['sha256'],
        previous_cohort=PREVIOUS.name,stopped_ancestor_coordinators=[dict(root=x['root'],pid=x['pid']) for x in ancestors],coordinator_script=__file__,coordinator_script_sha256=sha(__file__),
        user_instruction='你现在开始重跑实验吧，从那个连接拒绝的那个重新开始',
        network_isolation='private-netns-with-slirp4netns-outbound-stack/v1',
        host_8080_forwarding_modified=False,endpoint_proxy=False,api_configuration_changed=False,
        source_changes=['bench_tb_image_native.py: private network default and Docker hosts+resolver baseline with localhost preflight','tb_task_services.py: isolated usermode outbound stack'],
        original_frozen_worker_sources_unchanged=False,incident_repair_attempt_limit_per_cell=1,
        cumulative_all_attempt_costs=True,prior_results_immutable=True,
        retry_limit_per_cell='one ordinary failure retry plus one repair attempt for the recorded connection incident')
    save(ROOT/'registration.json',registration);save(ROOT/'source-manifest.json',manifest)
    inventory=json.loads(Path(reg['inventory']).read_text());evidence={r['task']:r['evidence'] for r in inventory['official_baseline_tasks']+inventory.get('audited_task_baseline_tasks',[])}
    active={}
    for retry,rows in [(False,cells),(True,retries)]:
        for row in rows:
            if row['state']=='running':
                active[row['worker_pid']]=dict(row=row,retry=retry,pid=row['worker_pid'],path=row['result_path'],proc=None,log=None,
                    started=datetime.datetime.fromisoformat(row['started_at']).timestamp(),adopted=True)
    assert len(active)<=8
    stop=None;last_review=0;last_health=0;api_health={};old_finalized=False;global_lock=None
    def progress():
        save(ROOT/'progress.json',dict(updated_at=now(),coordinator_pid=os.getpid(),counts=dict(collections.Counter(r['state'] for r in cells)),
            retry_counts=dict(collections.Counter(r['state'] for r in retries)),retry_tasks=retries,tasks=cells,
            active=[dict(task=i['row']['task'],harness=i['row']['harness'],policy=i['row']['policy'],pid=i['pid'],retry=i['retry'],adopted=i['adopted']) for i in active.values()],
            stop_reason=stop,api_health=api_health,network_isolation=registration['network_isolation'],disk_free_gib=shutil.disk_usage(D).free/2**30,formal_eligible=False))
    progress();save(D/'validation/active-tb-dual-native.json',dict(cohort=NAME,root=str(ROOT),canary=False,coordinator_pid=os.getpid(),activated_at=now(),retry_authorized=True))
    while True:
        for worker,item in list(active.items()):
            proc=item['proc'];alive=proc.poll() is None if proc else state(worker) not in (None,'Z')
            timed_out=alive and time.time()-item['started']>=CELL_TIMEOUT_S
            if alive and not timed_out:continue
            if timed_out:old.cleanup_timeout(item)
            if proc:proc.wait(timeout=20)
            if item['log']:item['log'].close()
            row=item['row'];path=Path(item['path'])
            if path.exists():result=json.loads(path.read_text())
            else:
                error=path.parent.parent/'host-error.txt'
                result=dict(completed=False,success=False,status='missing_worker_result',host_error=error.read_text()[-4000:] if error.exists() else 'Worker exited without host result; usage unknown',api_usage=dict(complete=False),task=row['task'],harness=row['harness'],policy=row['policy']);save(path,result)
            issue='worker_wall_timeout' if timed_out else infrastructure_issue(result,row['expected_context'])
            row.update(state='infrastructure_error' if issue else 'completed',issue=issue,result_path=str(path),finished_at=now(),worker_returncode=proc.returncode if proc else result.get('container_returncode'))
            if item['adopted']:
                prior=next(r for r in previous['retry_tasks' if item['retry'] else 'tasks'] if key(r)==key(row) and r.get('worker_pid')==worker);prior.update(copy.deepcopy(row))
            elif old.halts_dispatch(issue,result,old.model_budget_failure_evidence(result,path) if issue=='incomplete_api_usage' else None) or connection_evidence(row):
                # Halt new dispatch after a fresh environment exception; do not
                # silently consume the remaining grid during another outage.
                stop='new_environment_exception_requires_review'
                save(AUDIT/'dispatch-halt.json',dict(at=now(),row=row))
            del active[worker]
        if not old_finalized and not any(i['adopted'] for i in active.values()):
            for ancestor in ancestors:
                apid=ancestor['pid'];assert state(apid)=='T'
                os.kill(apid,signal.SIGTERM);os.kill(apid,signal.SIGCONT)
                for _ in range(50):
                    if state(apid) in (None,'Z'):break
                    time.sleep(.1)
                assert state(apid) in (None,'Z')
                if Path(ancestor['root'])!=PREVIOUS:
                    ap=ancestor['progress'];terminal={r.get('result_path'):r for r in cells+retries if r['state'] in TERMINAL}
                    for r in ap['tasks']+ap.get('retry_tasks',[]):
                        if r.get('result_path') in terminal:r.update(copy.deepcopy(terminal[r['result_path']]))
                    ap.update(updated_at=now(),active=[],stop_reason='network_isolation_continuation',counts=dict(collections.Counter(r['state'] for r in ap['tasks'])),retry_counts=dict(collections.Counter(r['state'] for r in ap.get('retry_tasks',[]))))
                    save(Path(ancestor['root'])/'progress.json',ap);save(Path(ancestor['root'])/'execution.json',dict(finished_at=now(),continuation=NAME,workers_interrupted=False))
            global_lock=(D/'validation/tb-dual-native.lock').open('a');fcntl.flock(global_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            previous.update(updated_at=now(),active=[],counts=dict(collections.Counter(r['state'] for r in previous['tasks'])),retry_counts=dict(collections.Counter(r['state'] for r in previous['retry_tasks'])),stop_reason='network_isolation_continuation')
            save(PREVIOUS/'progress.json',previous);save(PREVIOUS/'execution.json',dict(finished_at=now(),continuation=NAME,stop_reason='network_isolation_continuation',workers_interrupted=False))
            assert all(sha(p)==h for p,h in protected.items());save(AUDIT/'handoff-verification.json',dict(at=now(),protected_result_count=len(protected),all_hashes_unchanged=True,workers_interrupted=False))
            old_finalized=True
        old.add_retries(cells,retries);new=add_network_retries(cells,retries)
        if new:
            with (AUDIT/'network-repair-registrations.jsonl').open('a') as out:
                for row in new:out.write(json.dumps(row)+'\n')
        if time.monotonic()-last_health>=30:
            api_health=health();last_health=time.monotonic();save(AUDIT/'api-health.json',api_health)
        while not stop and api_health.get('healthy') and len(active)<8:
            selected=select(cells,retries,active)
            if not selected:break
            row,retry=selected;path=result_path(row,retry);folder=path.parent.parent;folder.parent.mkdir(parents=True,exist_ok=True)
            assert not folder.exists(),'Never reuse an existing attempt directory'
            logpath=ROOT/('network-retry-worker-logs' if row.get('network_repair_cohort') else 'retry-worker-logs' if retry else 'worker-logs')/row['task']/row['harness']/(row['policy']+'.log');logpath.parent.mkdir(parents=True,exist_ok=True)
            log=logpath.open('w');env=dict(os.environ,PYTHONPATH=f'{FROZEN}/src:{FROZEN}',AGENTTX_SKIP_ORPHAN_REAP='1',AGENTTX_NO_PROXY='1',NO_PROXY='*')
            for k in ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'):env.pop(k,None)
            proc=subprocess.Popen(cell_command(FROZEN,folder,row,evidence.get(row['task'])),cwd=FROZEN,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            if row.get('network_repair_cohort') and row['network_repair_cohort']!=NAME:
                row['original_network_repair_cohort']=row['network_repair_cohort'];row['network_repair_cohort']=NAME
            row.update(state='running',started_at=now(),worker_pid=proc.pid,result_path=str(path),execution_cohort=NAME)
            active[proc.pid]=dict(row=row,retry=retry,pid=proc.pid,path=str(path),proc=proc,log=log,started=time.time(),adopted=False)
            with (AUDIT/'dispatches.jsonl').open('a') as f:f.write(json.dumps(dict(at=now(),task=row['task'],harness=row['harness'],policy=row['policy'],attempt=row.get('attempt',0),retry=retry,pid=proc.pid,total_active=len(active),network_repair=row.get('network_repair_cohort')==NAME))+'\n')
        progress()
        if time.monotonic()-last_review>=60:
            save(ROOT/'effects.json',dict(checked_at=now(),comparisons=effects(cells,experiment_configuration()),token_definition='Original first attempts only; cumulative all-attempt accounting in retry-report.json.'))
            latest_retry={key(r):r for r in retries if r['state'] in TERMINAL}
            save(ROOT/'retry-effects.json',dict(checked_at=now(),comparisons=effects(list(latest_retry.values()),experiment_configuration())))
            save(ROOT/'retry-report.json',report(cells,retries));last_review=time.monotonic()
        if not active and (stop or not any(r['state']=='pending' for r in cells+retries)):break
        time.sleep(5)
    save(ROOT/'execution.json',dict(finished_at=now(),stop_reason=stop,first_attempt_counts=dict(collections.Counter(r['state'] for r in cells)),retry_counts=dict(collections.Counter(r['state'] for r in retries))))

if __name__=='__main__':main()
