"""Real adapter EXIT cleanup must not delete task-owned offline HF assets."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from agenttx.agents.external import CodexHarness
from experiments.scripts.bench_tb_image_native import configure_task_hf_cache, HF_CACHE_CONTRACT
from experiments.scripts import run_tb_dual_local as runner
from scripts.maintenance.tb_cache_comparisons_20261026 import separate_comparisons


@pytest.mark.parametrize('fixed', [False, True])
def test_offline_cache_survives_real_codex_exit_only_with_task_home(tmp_path, monkeypatch, fixed):
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path/'task-cache'))
    for name in ('HF_HOME', 'HF_HUB_CACHE', 'HUGGINGFACE_HUB_CACHE', 'ALL_PROXY', 'HTTPS_PROXY', 'HTTP_PROXY'):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv('AGENTTX_NO_PROXY', '1')
    for name in ('CODEX_AUTH_FILE', 'CODEX_CONFIG_FILE'):
        monkeypatch.setenv(name, '/dev/null')
    monkeypatch.setenv('CODEX_AUTH_MODE', 'api')
    if fixed:
        assert configure_task_hf_cache()['contract'] == HF_CACHE_CONTRACT
    fake = tmp_path/'fake_agent.py'
    fake.write_text('''from pathlib import Path
from huggingface_hub.constants import HF_HUB_CACHE
cache=Path(HF_HUB_CACHE)/'models--synthetic--fixture'
(cache/'refs').mkdir(parents=True)
(cache/'refs/main').write_text('a'*40)
snapshot=cache/'snapshots'/('a'*40)
snapshot.mkdir(parents=True)
(snapshot/'config.json').write_text('{"synthetic":true}')
''')
    command = CodexHarness(command=shlex.join([sys.executable, str(fake)]), model='synthetic').command('fixture', tmp_path)
    subprocess.run(command, cwd=tmp_path, check=True, capture_output=True, text=True)
    assert not list(tmp_path.glob('.agenttx-codex-*')), 'real EXIT trap must still clean adapter state'
    probe = '''from huggingface_hub import try_to_load_from_cache
import json
p=try_to_load_from_cache('synthetic/fixture','config.json')
print(json.dumps(bool(isinstance(p,str) and json.load(open(p))['synthetic'])))'''
    result=subprocess.run([sys.executable,'-c',probe],check=True,capture_output=True,text=True)
    assert json.loads(result.stdout) is fixed


def test_task_cache_respects_explicit_image_home(monkeypatch):
    monkeypatch.setenv('HF_HOME','/task/existing-hf')
    assert configure_task_hf_cache()['hf_home']=='/task/existing-hf'


def test_repaired_cache_histories_separate_without_dropping_failed_cost(tmp_path):
    cells=[]; retries=[]
    for i,(policy,fixed,tokens,target) in enumerate([
        ('causal',False,10,cells),('temporal_checkpoint',True,50,cells),('causal',True,20,retries)]):
        p=tmp_path/str(i)/'host-result.json';p.parent.mkdir()
        result=dict(runner.experiment_configuration(),success=True,completed=True,
                    api_usage=dict(complete=True,total_tokens=tokens))
        if fixed:result['hf_cache_contract']=HF_CACHE_CONTRACT
        p.write_text(json.dumps(result))
        target.append(dict(task='synthetic',harness='codex',policy=policy,state='completed',result_path=str(p)))
    report=separate_comparisons(dict(known_cumulative_tokens=80),cells,retries)
    assert report['known_cumulative_tokens']==80
    assert any(r['policy']=='causal' for r in report['cumulative_pair_exclusions'])
    assert all(v['both_success_pairs']==0 for g in report['cumulative_configuration_groups'].values()
               for policies in g['cumulative_paired_success'].values() for v in policies.values())
