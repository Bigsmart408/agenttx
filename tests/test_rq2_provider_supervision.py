import json

import pytest

from scripts.maintenance.supervise_rq2_turn_checkpoint_20261111 import classify, terminal_provider_error


def test_intermediate_gateway_error_does_not_replace_a_successful_terminal_event():
    events = [
        {'type': 'turn.failed', 'error': {'message': 'unexpected status 502 Bad Gateway'}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 2}},
    ]
    assert not terminal_provider_error(events)


def test_gateway_words_in_agent_output_do_not_authorize_a_restart():
    assert not terminal_provider_error([
        {'type': 'item.completed', 'item': {'text': 'unexpected status 502 Bad Gateway'}}])


@pytest.mark.parametrize('status', [502, 503, 504])
def test_explicit_terminal_gateway_failure_is_recognized(status):
    assert terminal_provider_error([
        {'type': 'turn.failed', 'error': {'message': f'unexpected status {status} from model endpoint'}}])


def test_provider_failure_does_not_bypass_context_mismatch(tmp_path):
    result = tmp_path / 'result.json'
    result.write_text(json.dumps(dict(
        recovery_context_variant='compact', environment_error=True,
        host_leak_before_commit=False, harness_returncode=1,
        harness_stdout=json.dumps({'type': 'turn.failed', 'error': {'message': 'unexpected status 502'}}))))
    assert classify(dict(benchmark='swe', harness='codex', result_path=str(result))) is None


@pytest.mark.parametrize('mutation', ['valid', 'too_early', 'unstructured', 'context_mismatch'])
def test_native_sweagent_deadline_requires_structured_registered_evidence(tmp_path, mutation):
    result = dict(recovery_context_variant='full', completed=True, container_returncode=0,
                  conversation_matches_workspace=True, api_usage={'complete': False},
                  model_returncode=124, model_timed_out=True, model_session_wall_s=3600)
    model = dict(returncode=124, harness='sweagent_native', duration_s=3631.8,
                 stderr='_SWEAgentWallTimeout: SWEAgent workload exceeded its wall-clock timeout',
                 metadata=dict(harness_backend='sweagent_native', native_adapter='SWEAgentNativeAdapter',
                               sweagent_exit_status='_SWEAgentWallTimeout'))
    if mutation == 'too_early':
        model['duration_s'] = 300
    elif mutation == 'unstructured':
        model['metadata']['sweagent_exit_status'] = 'unknown_error'
    elif mutation == 'context_mismatch':
        result['recovery_context_variant'] = 'compact'
    path = tmp_path / 'host-result.json'
    path.write_text(json.dumps(result))
    (tmp_path / 'model-result.json').write_text(json.dumps(model))
    classification = classify(dict(benchmark='tb', harness='sweagent', result_path=str(path)))
    assert classification == ('registered_native_model_session_timeout' if mutation == 'valid' else None)
