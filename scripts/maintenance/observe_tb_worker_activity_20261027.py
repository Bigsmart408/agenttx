"""Read-only process/trace activity, without model calls or command secrets."""
import concurrent.futures
import collections
import datetime
import json
import os
from pathlib import Path
import subprocess

D = Path('/mnt/data/agenttx-runtime')
AUDIT = D / 'validation/tb-live-monitor-20261026'
ENV = dict(os.environ, DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')
PROBE = r'''
import json, os, pathlib
rows=[]
for p in pathlib.Path('/proc').iterdir():
    if not p.name.isdigit() or int(p.name)==os.getpid():continue
    try:
        name=(p/'comm').read_text().strip()
        # Exclude this diagnostic and process short-lived inspection children.
        stat=(p/'stat').read_text().rsplit(') ',1)[1].split()
        io={k:int(v) for k,v in (line.split(':',1) for line in (p/'io').read_text().splitlines())}
        trace=[]
        for fd in (p/'fd').iterdir():
            try:
                target=fd.readlink()
                if target.name.startswith('worker-strace-') and target.suffix=='.log':
                    info=dict(line.split(':',1) for line in (p/'fdinfo'/fd.name).read_text().splitlines() if ':' in line)
                    trace.append(dict(name=target.name,position=int(info['pos']),bytes=fd.stat().st_size))
            except OSError:pass
        rows.append(dict(pid=int(p.name),name=name,state=stat[0],cpu_ticks=int(stat[11])+int(stat[12]),io=io,wchan=(p/'wchan').read_text().strip(),trace=trace))
    except (OSError,ValueError,IndexError):pass
network_counters={}
try:
    for line in pathlib.Path('/proc/net/dev').read_text().splitlines()[2:]:
        interface, values=line.split(':',1)
        fields=values.split()
        network_counters[interface.strip()]=dict(rx_bytes=int(fields[0]),tx_bytes=int(fields[8]))
except (OSError,ValueError,IndexError):pass
print(json.dumps(dict(processes=rows,network_counters=network_counters)))
'''


def inspect_worker(pair):
    row, container = pair
    result = dict(task=row['task'], harness=row['harness'], policy=row['policy'],
                  result_path=row['result_path'])
    if container is None:
        return dict(result, error='No active container for exact result mount')
    result['container'] = container['Id']
    result['network'] = container['HostConfig']['NetworkMode']
    try:
        proc = subprocess.run(['docker', 'exec', container['Id'], 'python3', '-c', PROBE],
                              env=ENV, text=True, capture_output=True, timeout=15)
        result['returncode'] = proc.returncode
        if proc.returncode == 0:
            observed = json.loads(proc.stdout)
            result['processes'] = observed['processes']
            result['network_counters'] = observed['network_counters']
        else:
            result['error'] = proc.stderr[-1000:]
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        result['error'] = str(exc)
    return result


def main():
    root = Path(json.loads((D/'validation/active-tb-dual-native.json').read_text())['root'])
    progress = json.loads((root/'progress.json').read_text())
    rows = [r for r in progress['tasks']+progress['retry_tasks'] if r['state']=='running']
    ids = subprocess.check_output(['docker','ps','-q'],env=ENV,text=True).split()
    containers = []
    if ids:
        # Containers can finish between listing and inspecting. Docker still
        # returns valid JSON for those that remain; retain that snapshot while
        # allowing only the expected disappearance error.
        inspection = subprocess.run(['docker','inspect',*ids], env=ENV,
                                    text=True, capture_output=True)
        if inspection.returncode and any(
            not line.startswith('error: no such object: ')
            for line in inspection.stderr.splitlines() if line.strip()
        ):
            raise RuntimeError('docker inspect failed: '+inspection.stderr[-1000:])
        containers = json.loads(inspection.stdout)
    pairs = [(r,next((c for c in containers if any(m['Source']==str(Path(r['result_path']).parent) for m in c['Mounts'])),None)) for r in rows]
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        workers = list(pool.map(inspect_worker,pairs))
    now = datetime.datetime.now(datetime.timezone.utc)
    result = dict(at=now.isoformat(),cohort=root.name,no_model_calls=True,
                  host_forwarding_modified=False,workers=workers)
    path = AUDIT/'worker-activity'/f'{now:%Y%m%dT%H%M%SZ}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(at=result['at'],path=str(path),workers=len(workers),errors=sum('error' in r for r in workers))))
    for row in workers:
        processes=row.get('processes',[])
        print(json.dumps(dict(task=row['task'],harness=row['harness'],policy=row['policy'],
            error=row.get('error'),process_names=dict(collections.Counter(p['name'] for p in processes)),
            network_counters=row.get('network_counters',{}),
            coordinator=next((dict(cpu_ticks=p['cpu_ticks'],state=p['state'],trace=p['trace']) for p in processes if p['pid']==1),None))))


if __name__ == '__main__':
    main()
