from types import SimpleNamespace

import pytest

from agenttx.ledger import Effect, EffectKind, Ledger
from experiments.scripts.bench_official_tasks import POLICY_MODES, _apply_blind_policy
from experiments.scripts.bench_tb_image_native import CONTEXTS
from experiments.workloads.recovery_inject import (
    DocSpec, build_recovery_manifest, document_content, render_recovery_manifest_prompt,
)


def test_selected_effects_never_uses_dependency_expansion():
    calls = []
    tx = SimpleNamespace(rollback_exact=lambda ids: calls.append(ids) or ids)
    targets, details = _apply_blind_policy(SimpleNamespace(harness=SimpleNamespace(tx=tx)),
        {'root_step': 3, 'oracle_invalid_steps': [3, 6, 8]}, 'root_only')
    assert targets == [3] and calls == [[3]]
    assert details['dependency_expansion'] is False
    assert details['fault_location_disclosed'] is False
    assert 'root_only' in POLICY_MODES and CONTEXTS['root_only'] == 'full'


@pytest.mark.parametrize('mutation', ['none', 'expanded', 'missing_derived', 'root_active', 'wrong_target'])
def test_selected_manifest_certifies_retained_faults_without_claiming_cleanup(tmp_path, mutation):
    ledger = Ledger()
    root = ledger.add_step('fault', [Effect(str(tmp_path/'source.py'), EffectKind.WRITE)])
    derived = ledger.add_step('derived', [Effect(str(tmp_path/'recovery_build/derived.txt'), EffectKind.WRITE)])
    doc = DocSpec('notes/design.md', 'design', 2)
    note = ledger.add_step('note', [Effect(str(tmp_path/doc.path), EffectKind.WRITE)])
    if mutation != 'root_active':
        ledger.mark_rolled_back([root.step_id])
    if mutation == 'expanded':
        ledger.mark_rolled_back([derived.step_id])
    targets = [derived.step_id] if mutation == 'wrong_target' else [root.step_id]
    manifest = build_recovery_manifest(policy='root_only', ledger=ledger,
        injected=dict(root_step=root.step_id, faulty_path='source.py',
            independent_steps=[note.step_id], oracle_invalid_steps=[root.step_id, derived.step_id],
            derived_paths=['recovery_build/derived.txt']),
        docs=[doc], document_contents={doc.path: document_content('design', 2, 'task')},
        workdir=tmp_path, rollback_targets=targets,
        path_exists={'source.py': True, 'recovery_build/derived.txt': mutation!='missing_derived'})
    assert manifest['authoritative'] == (mutation == 'none')
    assert not manifest['post_recovery_verified']
    item = manifest['invalidated'][1]
    assert item['reason'] == 'fault_dependent_retained'
    assert item['must_stay_absent']  # Final task acceptance still requires model cleanup.
    if mutation == 'none':
        prompt = render_recovery_manifest_prompt(manifest)
        assert 'source.py' not in prompt and 'recovery_build/derived.txt' not in prompt
        assert 'STATE MISMATCH' not in prompt


def test_terminal_retention_keeps_full_model_evidence_and_native_accounting(tmp_path):
    import gzip
    import json
    from experiments.scripts.retain_selected_effects_session import retain
    cohort = tmp_path/'new-cohort'
    cohort.mkdir()
    (cohort/'registration.json').write_text(json.dumps({'protocol':'agenttx.selected-effects-full/v1'}))
    folder = cohort/'cells/tb/codex/task/attempt-001'
    cell = folder/'native/root_only'
    session = cell/'session'
    transcript = session/'upperdir/app/.agenttx-codex-x/codex/sessions/2026/11/16/events.jsonl'
    transcript.parent.mkdir(parents=True)
    data = '{"type":"event_msg","payload":{"type":"token_count"}}\n'
    transcript.write_text(data)
    (session/'upperdir/large-package').write_bytes(b'package')
    (session/'layers').mkdir()
    (session/'agenttx.json').write_text('{"ledger":"preserved"}')
    traces=b'raw syscall evidence\n'*100
    (session/'worker-strace-1.log').write_bytes(traces)
    result=b'{"completed":true,"api_usage":{"complete":true,"total_tokens":42}}'
    (cell/'host-result.json').write_bytes(result)
    (cell/'model-result.json').write_text('{"events":["full trajectory"]}')
    (cell/'final-ledger.json').write_text('{"steps":[1,2,3]}')
    retain(cohort, folder)
    assert (cell/'host-result.json').read_bytes()==result
    assert (cell/'model-result.json').read_text()=='{"events":["full trajectory"]}'
    assert (cell/'final-ledger.json').exists() and (session/'agenttx.json').exists()
    assert transcript.read_text()==data
    assert not (session/'upperdir/large-package').exists()
    assert gzip.decompress((session/'worker-strace-1.log.gz').read_bytes())==traces
    retain(cohort, folder)  # An already recorded retention is idempotent.


def test_terminal_retention_rejects_other_cohort_paths(tmp_path):
    import json
    from experiments.scripts.retain_selected_effects_session import retain
    cohort=tmp_path/'new';cohort.mkdir()
    (cohort/'registration.json').write_text(json.dumps({'protocol':'agenttx.selected-effects-full/v1'}))
    with pytest.raises(AssertionError):
        retain(cohort,tmp_path/'historical/cells/tb/codex/task')
