## Official application workloads (SWE-Bench + Terminal-Bench)

The paper's application evaluation uses only SWE-Bench Lite and Terminal-Bench.
Mechanism microbenchmarks (isolation scaling, causal DAGs, coverage, WAL) remain
available below but are not additional application workloads.

The measurement is the same as the original recovery and token experiments:
inject a faulty producer, independent notes, a derived artifact, and a failing
official test; compare `causal`, `temporal_checkpoint`, and
`whole_branch_abort`; succeed only when the official verifier passes **and**
the independent notes remain.  `bare` / per-call `try` / session `try` stay in
the isolation-cost benches below.  A process SIGKILL is not the application
fault model.

Keep two grouping axes.  Official labels stay unmodified: SWE-Bench Lite by
`repo`, Terminal-Bench by `task.yaml` `difficulty` (easy/medium/hard) and
`category`.  AgentTX `short`/`medium`/`long` is only a length-budget line for
injected note size and max-turns; do not report it as the benchmark's own
difficulty.

The default model tiers are fixed for this round: DeepSeek Harness runs
`deepseek-v4-flash`, and Codex runs `gpt-5.6-luna`.  Motivation and evaluation use the full official catalogs (SWE-Bench Lite
and Terminal-Bench original-tasks); the recovery DAG is only a
controlled fault overlay used to compare rollback policies.  The compatibility
names `bench_token_recovery.py`, `bench_token_end_to_end.py`, and the scripts
under `motivation/` dispatch to this runner and no longer create the old
synthetic document/long-trajectory repositories.

```bash
# Prefetch official instances
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_official_tasks.py --oracle --preflight-only

# Oracle repair after causal / temporal / whole-session recovery
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_official_tasks.py --oracle --suite all

# Live-agent run with the real DeepSeek Harness (requires
# `/home/pengpeng/.agenttx_llm.env` or a project `.agent.env`)
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_official_tasks.py --suite all \
  --harness deepseek_harness

# The same workload through the official Codex CLI
PYTHONPATH=src:. /home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/bench_official_tasks.py --suite all \
  --harness codex

/home/pengpeng/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/plot_official_tasks.py
```

Results are separated by external harness under
`experiments/results/{deepseek_harness,codex}/official_tasks.{csv,json,md}`;
the token-specific aggregate is `official_token_summary.{csv,json,md}`, and the
oracle remains under `experiments/results/official/`. Codex's `--json` stream
supplies token usage. DeepSeek Harness persists usage in `session.jsonl` or
`session.jsonl.zstd`; AgentTX reads the log from the transaction upperdir,
prefers the final message over the duplicate chunk event for each turn/step,
and counts input, cache-read, cache-write, and output buckets. Every raw row
records `usage_source` (`dsh_session_jsonl`, `stdout_jsonl`, or `none`) so
missing usage is visible rather than silently estimated. The driver never
falls back to the old in-process LLM loop.

The harness adapters load credentials in this order: `AGENTTX_ENV_FILE`,
`<project>/.agent.env`, `<project>/.env`, `~/.agenttx_llm.env`, and
`/home/pengpeng/.agenttx_llm.env`.  Values are inherited by the child process
but are never printed.  If `agentTX-clash` is installed, every external
command is launched as `agentTX-clash run -- ...` so the benchmark uses the
same proxy path as the live agent.  Override it with `AGENTTX_CLASH_COMMAND`
when running elsewhere.

At this interface stage each external harness is recorded as one opaque task
boundary inside AgentTX.  The harness still owns its own turns, tools, and
retries; any machine-readable usage events are normalized into prompt,
completion, total, p50, p95, and p99 fields. DeepSeek cache buckets are part
of normalized prompt tokens, and the aggregate records the source used for
each group. Missing usage remains explicitly zero/unknown rather than being
estimated from final text. This boundary is
intentional: the paper measures tokens discarded by recovery policies, not
turn-equivalent replay.

Historical long-trajectory and synthetic token result files are retained for
provenance only.  They must not be regenerated or cited as application
workloads; use `bench_official_tasks.py` (or one of its compatibility entry
points) for all new data.

The legacy GitHub sidecar suite remains for historical CSVs; do not add it to
the paper as an application workload.

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


## Coverage and WAL matrices (Steps 30–31)

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_coverage_matrix.py
AGENTTX_WAL_REPEATS=5 PYTHONPATH=src:. python3 -u experiments/scripts/bench_wal_fault_matrix.py
PYTHONPATH=src:. python3 motivation/plot_token_end_to_end.py
```

Artifacts: `experiments/results/coverage_matrix.*`, `experiments/results/wal_fault_matrix.*`,
`experiments/results/deepseek/token_end_to_end.*`, `paper/img/FIG-Token-End-to-End.pdf`.
