# Experiment plan (minimal)

## Baselines
1. Bare
2. Session-try
3. Per-call-try
4. Docker/bubblewrap

## Workloads
- In-repo edit + test
- Hazardous cleanup / package install
- Long trajectory (>=20 tool calls)

## Metrics
- Safety: unapproved dangerous host writes
- Utility: task success, human interventions
- Perf: per-tool latency, E2E time, overlay setup share
- Correctness: injected conflicting writes vs rollback

## Multi-file refactor: AgentTX-LLM vs Aider

Script: `experiments/scripts/bench_refactor_compare.py`

- AgentTX path: tool calls go through shared semisolate + ledger; host stays clean until explicit commit.
- Aider baseline: direct host edits (`--yes-always --no-git`); no speculative isolation.
- Results: `experiments/results/refactor_agent_compare.{csv,md}`

