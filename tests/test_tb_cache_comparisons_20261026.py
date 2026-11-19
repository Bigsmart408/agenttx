import json

from experiments.scripts import run_tb_dual_local as runner
from scripts.maintenance.tb_cache_comparisons_20261026 import separate_comparisons


def test_cache_separates_latest_and_cumulative_pairs_and_preserves_spend(tmp_path):
    cells=[]; retries=[]
    def row(policy,warm,tokens,index,target):
        p=tmp_path/str(index)/'host-result.json';p.parent.mkdir()
        result=dict(runner.experiment_configuration(),success=True,completed=True,
                    api_usage=dict(complete=True,total_tokens=tokens))
        if warm:result['prerequisite_cache']=dict(cache_sha256='verified-cache')
        p.write_text(json.dumps(result))
        target.append(dict(task='hf',harness='codex',policy=policy,state='completed',result_path=str(p)))
        return result,p
    cold,p=row('causal',False,10,0,cells)
    warm,q=row('temporal_checkpoint',True,50,1,cells)
    assert runner.configuration_id(runner.result_configuration(cold,p))==runner.configuration_id(runner.experiment_configuration())
    assert runner.configuration_id(runner.result_configuration(warm,q))!=runner.configuration_id(runner.experiment_configuration())
    preinstalled={**warm,'prerequisite_cache':{**warm['prerequisite_cache'],'preparation':'offline-fixed-global-install/v1'}}
    assert runner.configuration_id(runner.result_configuration(preinstalled,q))!=runner.configuration_id(runner.result_configuration(warm,q))
    report=separate_comparisons(dict(known_cumulative_tokens=60),cells,retries)
    assert report['known_cumulative_tokens']==60
    assert all(v['both_success_pairs']==0 for g in report['cumulative_configuration_groups'].values() for ps in g['cumulative_paired_success'].values() for v in ps.values())
    row('causal',True,20,2,retries)
    report=separate_comparisons(dict(known_cumulative_tokens=80),cells,retries)
    assert report['known_cumulative_tokens']==80
    assert report['cumulative_pair_exclusions'][0]['policy']=='causal'


def test_matching_warm_histories_include_every_attempt(tmp_path):
    cells=[]; retries=[]
    for index,(policy,tokens,target) in enumerate([('causal',10,cells),('temporal_checkpoint',50,cells),('causal',20,retries)]):
        p=tmp_path/str(index)/'host-result.json';p.parent.mkdir()
        p.write_text(json.dumps(dict(runner.experiment_configuration(),success=True,
            prerequisite_cache=dict(cache_sha256='same'),api_usage=dict(complete=True,total_tokens=tokens))))
        target.append(dict(task='hf',harness='codex',policy=policy,state='completed',result_path=str(p)))
    report=separate_comparisons({},cells,retries)
    pair=next(iter(report['cumulative_configuration_groups'].values()))['cumulative_paired_success']['codex']['temporal_checkpoint']
    assert pair['causal_cumulative_tokens']==30
    assert pair['control_cumulative_tokens']==50
    assert pair['saving_pct']==40
