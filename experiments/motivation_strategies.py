"""Canonical strategy identifiers and display names for motivation experiments."""

MICROBENCHMARK_POLICIES = (
    "no_recovery",
    "root_only",
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "ideal_oracle",
)

CORRECT_RECOVERY_POLICIES = (
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "ideal_oracle",
)

# The paper-facing workload spans multiple turns, so every recovery boundary
# selects a distinct rollback set for at least part of the turn-count sweep.
MOTIVATION_POLICIES = (
    "no_recovery",
    "root_only",
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "ideal_oracle",
)

MOTIVATION_CORRECT_RECOVERY_POLICIES = (
    "turn_checkpoint_proxy",
    "temporal_step",
    "whole_branch",
    "ideal_oracle",
)

REAL_TRACE_POLICIES = CORRECT_RECOVERY_POLICIES

MOTIVATION_REPLAY_POLICIES = (
    "clean_recovery",
    *MOTIVATION_POLICIES,
)

# Keep the two implementation-oriented arms available for breakdowns and for
# resuming historical replay data. The default Motivation experiment uses
# MOTIVATION_REPLAY_POLICIES so its policy names match the analytical study.
REPLAY_POLICIES = (
    *MOTIVATION_REPLAY_POLICIES,
    "agenttx_causal",
    "crash_no_rollback",
)

POLICY_LABELS = {
    "clean_recovery": "Clean recovery",
    "crash_no_rollback": "Crash, no rollback",
    "no_recovery": "No recovery",
    "root_only": "Root only",
    "turn_checkpoint_proxy": "Turn checkpoint",
    "temporal_step": "Temporal step",
    "whole_branch": "Whole branch",
    "agenttx_causal": "AgentTX causal",
    "ideal_oracle": "Ideal oracle",
}
