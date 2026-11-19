import json

from scripts.maintenance.retain_tb_summaries_20261022 import model_summary, retain


def test_failed_tool_and_final_message_survive_summary_without_full_trace(tmp_path):
    path=tmp_path/'model-result.json'
    secret='sk-'+'z'*32
    failed=dict(type='item.completed',item=dict(type='command_execution',command='private command body',exit_code=1,status='completed',aggregated_output='x'*10000+'ImportError '+secret))
    reply=dict(type='item.completed',item=dict(type='agent_message',text='Unable to import the training package.'))
    path.write_text(json.dumps(dict(returncode=0,stdout='\n'.join(map(json.dumps,[failed,reply])),events=[failed,reply])))
    summary=model_summary(path);diag=summary['bounded_diagnostics']
    assert diag['observed_command_results']==1
    assert diag['command_exit_counts']=={'1':1}
    assert 'ImportError' in diag['last_tool_results'][0]['output_excerpt']
    assert secret not in json.dumps(summary)
    assert 'private command body' not in json.dumps(summary)
    assert len(diag['last_tool_results'][0]['output_excerpt'])<=400
    assert diag['final_assistant_excerpt']=='Unable to import the training package.'
    assert 'stdout' not in summary and 'events' not in summary


def test_terminal_retention_keeps_host_result_and_bounded_diagnostics(tmp_path):
    result=dict(workspace='/app',success=False,model_calls=0,api_usage=dict(complete=False))
    path=tmp_path/'host-result.json';original=json.dumps(result);path.write_text(original)
    (tmp_path/'model-result.json').write_text(json.dumps(dict(events=[dict(type='item.completed',item=dict(type='agent_message',text='No adapter generated.'))])))
    retain(tmp_path,'ideal_oracle','codex',allow_test=True)
    assert path.read_text()==original
    assert not (tmp_path/'model-result.json').exists()
    summary=json.loads((tmp_path/'accounting-summary.json').read_text())['model_summaries']['model-result.json']
    assert summary['bounded_diagnostics']['final_assistant_excerpt']=='No adapter generated.'
    assert retain(tmp_path,'ideal_oracle','codex',allow_test=True) is None
