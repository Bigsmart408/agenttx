"""Shared bar colors from Figure 10 (real-world token comparisons)."""

BAR_PALETTE = {
    "red": "#b42318",
    "orange": "#d97706",
    "teal": "#16837a",
    "purple": "#7c5aa6",
    "gold": "#b8a05d",
}

BAR_POLICY_COLORS = {
    "causal": BAR_PALETTE["red"],
    "turn_checkpoint": BAR_PALETTE["purple"],
    "temporal_checkpoint": BAR_PALETTE["orange"],
    "whole_branch_abort": BAR_PALETTE["teal"],
    "root_only": BAR_PALETTE["gold"],
    "clean_recovery": BAR_PALETTE["purple"],
}
