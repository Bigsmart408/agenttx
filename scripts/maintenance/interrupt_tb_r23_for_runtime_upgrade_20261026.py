"""Preserve interrupted r23 attempts while switching validated runtime repairs."""
import concurrent.futures
import datetime
import json
import os
from pathlib import Path
import subprocess

D=Path('/mnt/data/agenttx-runtime')
ROOT=D/'results/tb-dual-native-full-20261026-r23-runtime-repair'
AUDIT=D/'validation/tb-file-race-memory-repair-20261026'
ENV=dict(os.environ,DOCKER_HOST='unix:///run/agenttx-docker/docker.sock')

def main():
    pointer=json.loads((D/'validation/active-tb-dual-native.json').read_text())
    assert Path(pointer['root'])==ROOT
    progress=json.loads((ROOT/'progress.json').read_text())
    assert progress['stop_reason']=='new_environment_exception_requires_review'
    assert not (AUDIT/'interrupted-active-attempts.json').exists()
    at=datetime.datetime.now(datetime.timezone.utc).isoformat()
    attempts=[]
    for row in progress['tasks']+progress['retry_tasks']:
        if row['state']!='running':continue
        result=Path(row['result_path']);folder=result.parent
        assert result.is_relative_to(ROOT)
        if result.exists():continue
        cid=(folder/'container.cid').read_text().strip()
        inspection=json.loads(subprocess.check_output(['docker','inspect',cid],env=ENV,text=True))[0]
        assert inspection['HostConfig']['NetworkMode']!='host'
        assert any(m['Destination']=='/results' and Path(m['Source']).resolve()==folder.resolve() for m in inspection['Mounts'])
        existing=folder/'result.json'
        marker=dict(at=at,reason='validated_live_file_race_and_streaming_memory_upgrade',model_interrupted=True,
            recorded_usage_is_lower_bound=True,all_prior_outcomes_and_costs_retained=True,
            exclude_incomplete_billing_from_complete_pairs=True,previous_model_result_preserved=existing.exists())
        (folder/'runtime-repair-interruption.json').write_text(json.dumps(marker,indent=2)+'\n')
        if not existing.exists():
            registration=json.loads((folder.parent/'registration.json').read_text())
            seed=dict(completed=False,success=False,status='infrastructure_error',
                error='runtime_environment_repair_interruption',
                environment_failure='live_file_race_and_unbounded_file_trace_memory_upgrade',
                task=row['task'],harness=row['harness'],policy=row['policy'],
                workspace=registration['image']['workdir'],context_variant=row['expected_context'],
                model_calls=-1,doc_replay_model_calls=0,model_interrupted=True,interrupted_at=at,
                api_usage=dict(complete=False),cost_policy='Recorded usage is a lower bound; preserve and exclude incomplete bills from complete-cost pairs.')
            existing.write_text(json.dumps(seed,indent=2)+'\n')
        attempts.append(dict(row=row,cid=cid,marker=marker))
    (AUDIT/'interrupted-active-attempts.json').write_text(json.dumps(dict(at=at,attempts=attempts),indent=2)+'\n')
    def stop(attempt):
        result=subprocess.run(['docker','stop','--time','2',attempt['cid']],env=ENV,text=True,capture_output=True,timeout=30)
        return dict(task=attempt['row']['task'],harness=attempt['row']['harness'],policy=attempt['row']['policy'],returncode=result.returncode)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        outcomes=list(pool.map(stop,attempts))
    print(json.dumps(outcomes))

if __name__=='__main__':main()
