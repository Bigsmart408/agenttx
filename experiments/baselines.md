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
