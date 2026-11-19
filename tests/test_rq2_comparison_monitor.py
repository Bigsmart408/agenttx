import json

from scripts.maintenance.watch_rq2_comparisons import compare, exclusion, history_summary, project


def observation(**changes):
    row = dict(task='task', benchmark='tb', harness='codex', policy='turn_checkpoint',
               state='completed', success=True, complete_usage=True, tokens=100,
               duration_s=5, model='model', context='full', trial_seed=42,
               configuration_id='same')
    row.update(changes)
    return row


def test_retry_costs_include_failed_attempt_and_latest_outcome():
    rows = [observation(success=False, tokens=30), observation(tokens=70)]
    summary = history_summary(rows)
    assert summary['success']
    assert summary['cumulative_tokens'] == 100
    assert summary['cumulative_duration_s'] == 10
    assert not history_summary(list(reversed(rows)))['success']


def test_unknown_usage_is_not_zero_and_cannot_be_paired():
    turn = history_summary([observation(complete_usage=False, tokens=20), observation()])
    baseline = history_summary([observation(policy='causal')])
    assert turn['cumulative_tokens'] is None
    assert exclusion(turn, baseline) == 'incomplete_usage_or_infrastructure_history'


def test_environment_change_in_any_attempt_excludes_pair():
    turn = history_summary([observation(configuration_id='old'), observation()])
    baseline = history_summary([observation(policy='causal')])
    assert exclusion(turn, baseline) == 'mixed_environment_history'
    turn = history_summary([observation(configuration_id='different')])
    assert exclusion(turn, baseline) == 'environment_or_budget_mismatch'


def test_trial_mismatch_excluded_but_expected_context_difference_is_explicit():
    turn = history_summary([observation()])
    baseline = history_summary([observation(policy='causal', context='compact', trial_seed=43)])
    assert exclusion(turn, baseline) == 'fault_trial_mismatch'
    baseline['trial_seed'] = 42
    output = compare({'tb/codex/task': turn}, {'tb/codex/task/causal': baseline})
    pair = output['tb/codex']['policies']['causal']['pairs'][0]
    assert (pair['baseline_context'], pair['turn_context']) == ('compact', 'full')


def test_failed_tasks_retained_in_outcomes_and_excluded_from_cost_pairs():
    turn = history_summary([observation(success=False)])
    baseline = history_summary([observation(policy='causal')])
    result = compare({'tb/codex/task': turn}, {'tb/codex/task/causal': baseline})
    policy = result['tb/codex']['policies']['causal']
    assert policy['matched_terminal_tasks'] == 1
    assert policy['turn_success_on_matched_tasks'] == 0
    assert policy['baseline_success_on_matched_tasks'] == 1
    assert policy['eligible_cost_pairs'] == 0
    assert policy['excluded'] == {'not_both_successful': 1}


def test_sweagent_uses_returned_usage_instead_of_summary(tmp_path):
    path = tmp_path / 'result.json'
    path.write_text('{}')
    (tmp_path / 'worker.log').write_text(
        'usage=Usage(completion_tokens= 5, prompt_tokens= 10, total_tokens= 15)\n'
        'usage=Usage(completion_tokens= 8, prompt_tokens= 12, total_tokens= 20)\n')
    raw = dict(task='task', mode='turn_checkpoint', total_tokens=25, doc_replay_tokens=0,
               doc_replay_prompt_tokens=0, doc_replay_completion_tokens=0,
               model_calls=2, doc_replay_tool_calls=0, harness_returncode=0,
               finished=True, usage_source='sweagent_model_stats')
    result = project(raw, path, 'swe', 'sweagent', 'completed')
    assert result['tokens'] == 35
    assert result['reported_tokens'] == 25
    assert result['complete_usage']
    raw['model_calls'] = 3
    assert not project(raw, path, 'swe', 'sweagent', 'completed')['complete_usage']


def test_codex_intermediate_usage_does_not_count_as_complete_terminal_usage(tmp_path):
    path = tmp_path / 'result.json'
    path.write_text('{}')
    raw = dict(task='task', mode='turn_checkpoint', total_tokens=30, doc_replay_tokens=0,
               doc_replay_prompt_tokens=0, doc_replay_completion_tokens=0,
               finished=True, harness_returncode=0, usage_source='stdout_jsonl',
               harness_stdout=json.dumps(dict(type='item.completed', usage=dict(input_tokens=20, output_tokens=10))))
    assert not project(raw, path, 'swe', 'codex', 'completed')['complete_usage']
