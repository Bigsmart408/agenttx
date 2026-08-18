# Experiments

The Chinese experiment guide `docs/experiments-explained.md` explains the
terminology first, then connects the motivation, optimization, causal-recovery,
real-agent, robustness, and token experiments into one paper evidence chain.

## Step 1 — `try` overhead curve

Goal: quantify why naive per-tool-call `try` is not enough for AgentTX.

```bash
cd /home/pengpeng/agenttx
python3 experiments/scripts/bench_try_overhead.py -n 20 --repeats 3
```

Baselines measured: `bare`, `per_call_try`, `session_try`.

Temps live under `/tmp/agenttx-*` and are deleted by the script.
Only curated CSVs under `experiments/results/` are kept for analysis.
Do not push until explicitly requested.


## Step 2 — shared overlay + ledger

```bash
cd /home/pengpeng/agenttx
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

## Step 27 — eBPF vs strace dependency tracing

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_bpf_trace.py \
  --steps 20 --repeats 3 --workload read
PYTHONPATH=src:. python3 motivation/plot_bpf_trace.py
```

Requires root and bpftrace; without them the benchmark exits non-zero and
writes no results. Compares no tracing, strace, and the session-persistent
eBPF backend on a read-heavy step, verifies READ + NEGATIVE capture fidelity per step,
and writes `experiments/results/bpf_trace_overhead.{csv,json,md}`.
The runtime selects the backend per session (`--trace-backend
{auto,strace,bpf}`; `bpf` is always session-persistent. See
`docs/step28-persistent-ebpf.md` and
`docs/step29-hardlink-preserving-transactions.md`.

## Step 12 ? content-addressed snapshot storage

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_snapshot_storage.py
```

The benchmark reports logical snapshot payload, physical unique blob bytes,
and the deduplication ratio in `experiments/results/snapshot_storage.{csv,md}`.

## Step 15 ? comparison matrix

This is the primary comparison for the paper's causal-recovery claim. It
separates runtime overhead from recovery semantics on a fixed `a -> b`,
independent `c`, then failure trajectory.

```bash
PYTHONPATH=src:. python experiments/scripts/bench_comparison_matrix.py --repeats 3 --n 10
```

The supported VM matrix is: `bare`, `per_call_try`, `session_try`,
`shared_try`, `shared_checkpoint`, `bubblewrap`, `agenttx_without_read_tracing`,
and `agenttx_full`. Results are written to
`experiments/results/comparison_matrix.{csv,json,md}`.

Interpretation is intentionally split: Session try and bubblewrap are useful
isolation/abort references but do not implement tool-boundary causal recovery;
`agenttx_without_read_tracing` is an ablation and should fail to remove the
derived `b` result. See `docs/step15-comparison-experiments.md`.

## Step 16 ? longer Agent workload

The original 28-step coding trace remains available as a smoke test. The longer
workload adds a multi-file refactor, an injected failing CI loop, an artifact
that reads the faulty file, independent docs/config edits, deletion, and a
repair suffix. It is parameterized by `--length` (default 64; minimum 54).

```bash
PYTHONPATH=src:. python3 -m pytest -q tests/test_long_workload.py
PYTHONPATH=src:. python3 experiments/scripts/bench_long_trajectory.py \
  --length 64 --repeats 1
```

The benchmark compares `bare`, `per_call_try`, `shared_try`,
`shared_checkpoint`, `agenttx_without_read_tracing`, and `agenttx_full`. It
records runtime, host pollution, ledger/read-effect counts, and whether causal
rollback removes the faulty formatter plus its derived report while retaining
independent docs/config files. Results are written to
`experiments/results/long_workload_matrix.{csv,json,md}`. See
`docs/step16-long-agent-workloads.md`.

## Step 17 ? scaling, variance, tracing, and storage

These experiments do not add external comparison systems. They extend the long
workload to 54/64/96 calls with two repeats, and refresh the existing scaling,
read-tracing, and content-addressed snapshot measurements.

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_long_scaling.py \
  --lengths 54 64 96 --repeats 2
PYTHONPATH=src:. python3 experiments/scripts/bench_scaling.py
PYTHONPATH=src:. python3 experiments/scripts/bench_trace_overhead.py \
  --steps 20 --repeats 3
PYTHONPATH=src:. python3 experiments/scripts/bench_snapshot_storage.py
```

Results: `long_workload_scaling.{csv,json,md}`, `scaling_curve.{csv,md}`,
`trace_overhead.{csv,md}`, and `snapshot_storage.{csv,md}`. See
`docs/step17-evaluation-scaling.md`.

## Step 18 ? optimization iteration history

Performance changes preserve a source snapshot before each iteration under
`src/agenttx/optimization_history/`. The first two low-risk changes make known
harness effects explicit and keep opaque shell/test tracing intact. See
`docs/step18-optimization-iterations.md` for before/after measurements and the
remaining incremental-snapshot/worker optimizations.

## Step 24 — real-agent replay-token savings

This experiment isolates the LLM work discarded by recovery granularity. It
uses the real AgentTX overlay and dependency graph for all policies, then asks
`deepseek-v4-flash` to regenerate only valid documents that the selected policy
lost. The sweep varies each document from 12 to 48 distinct entries and records
actual API prompt/completion/total tokens, tool calls, retries, p50/p95, tests,
and pre-commit host leakage.

```bash
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_token_recovery.py \
  --document-lines 12 24 48 --repeats 3
```

Results are written to `experiments/results/token_recovery.{csv,json,md}` and
`token_recovery_raw.csv`. `temporal_checkpoint` and `whole_branch_abort` are
recovery-granularity emulations, not executions of external artifacts. See
`docs/step24-token-replay-evaluation.md` for the metric boundary and SOTA
mapping.

## Step 26 — end-to-end autonomous recovery tokens

This companion comparison keeps the full post-policy LLM loop intact. The same
agent diagnoses, uses tools, validates, and repairs the same recovered workspace
under causal, optimistic temporal-checkpoint, and whole-branch policies. It
records complete prompt/completion/total API usage, model/tool calls, ledger
steps, regenerated documents, success, host leakage, and recovery p50/p95.

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_token_end_to_end.py \
  --document-lines 12 24 48 --repeats 3 --max-turns 20
```

A credentialed, preflight-clean run writes `token_end_to_end.{csv,json,md}` and
`token_end_to_end_raw.csv`. Use `--preflight-only` to diagnose missing
`strace`/`try`/overlay support. On this x86 Docker-overlay host, execute the
preflight and benchmark as root while preserving the `agenttx` conda PATH;
`scripts/bootstrap.sh` reapplies the `try` compatibility patch after a fresh
clone. Missing credentials or a failed preflight now return non-zero; add
`--allow-skip` only for optional CI. See
`docs/step26-end-to-end-token-comparison.md`.

## Paper-facing notebooks

The result files remain the source of truth. The notebooks under `motivation/`
are deterministic presentation layers and can be executed independently:

- `plot_causal_retention.ipynb` — controlled DAG retention/removal and rollback
  latency;
- `plot_token_recovery.ipynb` — real replay tokens, regenerated documents, and
  replay p95;
- `plot_token_end_to_end.ipynb` — complete autonomous recovery prompt,
  completion, total tokens, and recovery p95;
- `plot_real_agent_recovery.ipynb` — live LLM root selection, rollback targets,
  latency, and recovery invariants;
- `plot_bpf_trace.ipynb` — persistent eBPF vs strace vs no-tracing cost,
  incremental tax, and capture fidelity (requires `bpf_trace_overhead.csv`);
- `plot_robustness.ipynb` — p50/p95, worker crash, 256-step resume, and four
  concurrently isolated agents.

The hardlink-alias probe is deliberately not plotted: it is a single semantic
counterexample defining the inode-alias boundary, not a quantitative sweep.

## tiao2 remote comparison refresh

The ARM64 `tiao2` run, including the root-compatible `try` profile, refreshed
the primary comparison matrix, 64-step workload, scaling, causal-retention,
tracing, snapshot, and robustness artifacts.  It also records the attempted
BranchFS build and the Waypoint/CRIU blocker without fabricating external
numbers.  See [`docs/tiao2-comparison-run.md`](../docs/tiao2-comparison-run.md)
for the exact host profile, commands, results, and external-baseline status.
