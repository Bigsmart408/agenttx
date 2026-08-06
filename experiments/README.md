# Experiments

## Step 1 — `try` overhead curve

Goal: quantify why naive per-tool-call `try` is not enough for AgentTX.

```bash
cd /home/bfq/agenttx
python3 experiments/scripts/bench_try_overhead.py -n 20 --repeats 3
```

Baselines measured: `bare`, `per_call_try`, `session_try`.

Temps live under `/tmp/agenttx-*` and are deleted by the script.
Only curated CSVs under `experiments/results/` are kept for analysis.
Do not push until explicitly requested.


## Step 2 — shared overlay + ledger

```bash
cd /home/bfq/agenttx
PYTHONPATH=src python3 experiments/scripts/test_ledger.py
PYTHONPATH=src python3 experiments/scripts/demo_trajectory.py
PYTHONPATH=src python3 experiments/scripts/bench_shared_overlay.py 20 3
```

Compares `per_call_try` vs `shared_overlay` (`try -N` reuse + effect ledger).


## Step 4 — coding agent + long trajectory

```bash
PYTHONPATH=src python3 tests/test_policy.py
PYTHONPATH=src:. python3 experiments/scripts/demo_coding_agent.py
PYTHONPATH=src:. python3 experiments/scripts/bench_long_trajectory.py 2
```

## Evidence suite (stronger claims)

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_evidence_suite.py
PYTHONPATH=src:. python3 experiments/scripts/bench_scaling.py
```

Results: `experiments/results/evidence_suite.*`, `experiments/results/scaling_curve.*`.
See also `docs/STATUS.md` for completed vs remaining.



## Step 7 ? automatic dependency-tracing overhead

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_trace_overhead.py -n 10 --repeats 3
```

Compares the same shared AgentTX no-op trajectory with automatic workspace
read/negative tracing disabled and enabled. Results are written to
`experiments/results/trace_overhead.{csv,md}`.

## Step 12 ? content-addressed snapshot storage

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_snapshot_storage.py
```

The benchmark reports logical snapshot payload, physical unique blob bytes,
and the deduplication ratio in `experiments/results/snapshot_storage.{csv,md}`.
