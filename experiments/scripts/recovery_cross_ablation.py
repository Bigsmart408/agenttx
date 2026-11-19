"""Experiment-only filesystem/context factorial; production recovery is unchanged.

The original policy runs, including normal context rewind and state checks,
finish before the context treatment is applied at the model handoff.  Stale
arms restore only the original observations, retaining subsequent replay and
the same neutral recovery notice.  Every arm uses full history formatting.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
from pathlib import Path

VARIANTS = {
    'no_rollback': ('crash_no_rollback', False),
    'context_only': ('crash_no_rollback', True),
    'filesystem_only': ('causal', False),
    'agenttx': ('causal', True),
}
NOTICE = 'Continue the original task from the current file state.'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def treatment_conversation(before, aligned, synchronize):
    """Keep later replay/observations and change original-call visibility only."""
    from agenttx.conversation import ConversationLog
    current = ConversationLog.from_dict(copy.deepcopy(aligned))
    for turn in current.turns:
        if turn.kind == 'recovery' and turn.status == 'active':
            turn.messages = [dict(role='user', content=NOTICE)]
    if synchronize:
        return current
    stale = ConversationLog.from_dict(copy.deepcopy(before))
    original_ids = {t.turn_id for t in stale.turns}
    stale.turns.extend(copy.deepcopy(t) for t in current.turns if t.turn_id not in original_ids)
    stale.generation = current.generation
    stale.last_recovery_mode = current.last_recovery_mode
    return stale


def neutral_manifest(manifest):
    result = copy.deepcopy(manifest)
    result['policy'] = 'comparison'
    result['state_id'] = 'checked-current-files'
    result['localized_fault_step_ids'] = []
    result['fault_location_source'] = 'hidden'
    for item in result.get('retained', []):
        item['origin'] = 'checked_current_file'
    return result


def handoff(manifest):
    """One template without policy labels, localization hints, or prompt compaction."""
    if not manifest.get('authoritative'):
        raise RuntimeError('cross ablation requires a verified filesystem handoff')
    completed = sorted((dict(path=x['path'], contract=x['contract']) for x in
                        manifest.get('retained', [])), key=lambda x: x['path'])
    missing = sorted((dict(path=x['path'], contract=x['contract']) for x in
                      manifest.get('recreate_required', [])), key=lambda x: x['path'])
    return ('Current checked recovery-note state:\n'
            + json.dumps(dict(completed_notes=completed, missing_notes=missing), ensure_ascii=False)
            + '\nCompleted notes are valid: preserve them without reopening or rewriting them. '
              'Recreate listed missing notes only when absent. Continue the original task '
              'using its official verifier. The recovery decision has already been made; '
              'do not invoke rollback tools. No fault location is supplied in this handoff.')


def history_prompt(conversation, prompt):
    from agenttx.conversation import ConversationLog
    view = ConversationLog.from_dict(conversation.to_dict())
    view.turns = [t for t in view.turns if t.kind != 'recovery']
    return ('Recorded task tool history:\n' + json.dumps(view.active_messages(), ensure_ascii=False)
            + '\n\nCurrent task and filesystem handoff:\n' + prompt)


class Treatment:
    def __init__(self, variant, output):
        self.variant = variant
        self.policy, self.synchronize = VARIANTS[variant]
        self.restore_files = self.policy == 'causal'
        self.output = Path(output)
        self.tx = self.before = self.injected = self.targets = None
        self.applied = False

    def capture(self, function):
        @functools.wraps(function)
        def call(agent, injected, *args, **kwargs):
            tx = agent.harness.tx
            before = copy.deepcopy(tx.conversation.to_dict())
            self.tx, self.before, self.injected = tx, before, copy.deepcopy(injected)
            self.injected_files = self.file_state()
            self.planned_targets = []
            if self.synchronize and not self.restore_files:
                # Discover the same verified plan as AgentTX in isolated trials,
                # but do not apply it to live files or ledger status. Context is
                # changed only at the final handoff, after native state checks.
                apply_plan = tx.apply_recovery_plan

                def select_only(plan):
                    if not plan.verified:
                        raise RuntimeError('context-only arm requires a verified plan')
                    self.planned_targets = list(plan.rollback_targets)
                    return []

                tx.apply_recovery_plan = select_only
                try:
                    result = self.localize(agent, injected)
                finally:
                    tx.apply_recovery_plan = apply_plan
            else:
                result = function(agent, injected, *args, **kwargs)
                self.planned_targets = list(result[0])
            self.rollback_ids = list(result[0])
            self.targets = list(self.planned_targets)
            self.applied = False
            self.policy_files = self.file_state()
            if not self.restore_files and (self.rollback_ids or self.policy_files != self.injected_files):
                raise RuntimeError('file-disabled treatment changed the injected filesystem')
            self.output.mkdir(parents=True, exist_ok=True)
            (self.output/'cross-precrash-conversation.json').write_text(json.dumps(before, indent=2))
            return result
        return call

    def file_state(self):
        tx, injected = self.tx, self.injected
        names = {injected['faulty_path'], *injected.get('derived_paths', []),
                 *injected.get('docs_before', []), *injected.get('docs_after', [])}
        state = {}
        for name in sorted(names):
            path = Path(name)
            if not path.is_absolute():
                path = Path(tx.workspace)/path
            entry = tx._overlay_entry(path)
            state[name] = (hashlib.sha256(entry.read_bytes()).hexdigest()
                           if entry is not None and entry.is_file() else None)
        return state

    def apply(self):
        if self.tx is None or self.applied:
            return
        aligned = copy.deepcopy(self.tx.conversation.to_dict())
        if self.synchronize and not self.restore_files:
            from agenttx.conversation import ConversationLog
            context = ConversationLog.from_dict(aligned)
            context.rewind(self.targets, notice=NOTICE, expand_dependencies=False)
            aligned = context.to_dict()
        before_files = self.file_state()
        self.tx.conversation = treatment_conversation(self.before, aligned, self.synchronize)
        self.tx._persist()
        after_files = self.file_state()
        if before_files != after_files:
            raise RuntimeError('context treatment modified filesystem state')
        original = {int(x) for x in self.injected['independent_steps']}
        original.update(int(x) for x in self.injected['oracle_invalid_steps'])
        actual = set(self.tx.conversation.active_step_ids()) & original
        expected = original - set(self.targets) if self.synchronize else original
        if actual != expected:
            raise RuntimeError('cross ablation did not produce the registered visible call set')
        signature = {k: self.injected.get(k) for k in ('root_step', 'independent_steps',
            'independent_before_steps', 'independent_after_steps', 'oracle_invalid_steps',
            'docs_before', 'docs_after', 'derived_paths')}
        self.proof = dict(protocol='agenttx.recovery-cross-treatment/v2', variant=self.variant,
            policy=self.policy, restore_files=self.restore_files,
            synchronize_context=self.synchronize, history_format='full',
            injection_signature=signature, injection_signature_sha256=digest(signature),
            original_step_ids=sorted(original), rollback_ids=self.rollback_ids,
            selected_causal_ids=self.targets,
            visible_original_step_ids=sorted(actual), expected_visible_original_step_ids=sorted(expected),
            filesystem_before=before_files, filesystem_after=after_files,
            filesystem_after_policy=getattr(self, 'policy_files', before_files),
            filesystem_after_injection=getattr(self, 'injected_files', before_files),
            file_disabled_preserves_injected_files=(None if self.restore_files else
                self.injected_files == before_files == after_files),
            context_treatment_preserves_files=True, original_observation_treatment_verified=True,
            recovery_notice=NOTICE, localization_hints_in_handoff=False,
            official_model_calls_started=False)
        for name, value in [('cross-treatment.json', self.proof),
                            ('cross-aligned-conversation.json', aligned),
                            ('cross-model-conversation.json', self.tx.conversation.to_dict())]:
            (self.output/name).write_text(json.dumps(value, indent=2, ensure_ascii=False)+'\n')
        self.applied = True


def install(variant, output, *, harness):
    """Install hooks in this experiment worker, never in another running cohort."""
    from experiments.scripts import bench_official_tasks as bench
    from experiments.workloads import recovery_inject as injection
    from experiments.workloads import swe_bench_suite as swe
    os.environ['AGENTTX_RECOVERY_CONTEXT'] = 'full'
    treatment = Treatment(variant, output)
    treatment.localize = bench._auto_localize_policy
    bench._auto_localize_policy = treatment.capture(bench._auto_localize_policy)
    bench._apply_blind_policy = treatment.capture(bench._apply_blind_policy)
    original_build = injection.build_recovery_manifest

    @functools.wraps(original_build)
    def build(**kwargs):
        result = original_build(**kwargs)
        if treatment.tx is not None and kwargs['ledger'] is treatment.tx.ledger:
            treatment.apply()
        return result

    injection.build_recovery_manifest = bench.build_recovery_manifest = build
    injection.render_recovery_manifest_prompt = handoff
    original_task_prompt = swe.task_prompt

    @functools.wraps(original_task_prompt)
    def task_prompt(*args, **kwargs):
        kwargs['mode'] = 'temporal_checkpoint'
        kwargs['recovery_manifest'] = neutral_manifest(kwargs['recovery_manifest'])
        prompt = original_task_prompt(*args, **kwargs).replace(
            'This coarse policy used only its configured rollback boundary; '
            'no fault step or source path is disclosed.',
            'No fault step or source path is disclosed.').replace(
            'The runtime completed the selected recovery policy and generated a '
            'context-aligned state certificate for this fresh session.',
            'The runtime checked the recovery-note files for this session.')
        if harness == 'codex':
            prompt = history_prompt(treatment.tx.conversation, prompt)
        (Path(output)/'cross-official-prompt.txt').write_text(prompt)
        return prompt

    swe.task_prompt = task_prompt
    return treatment
