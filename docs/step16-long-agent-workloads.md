# Step 16 — 64-tool-call Coding Agent workload

## Why this workload exists

The original coding trajectory was a useful smoke test, but it was too short and
mostly successful. OSDI evaluation needs a workload where a coding agent has to
carry state across many tool boundaries, encounter a failing CI check, make
independent progress, and then repair only the causal fault. Step 16 adds that
missing case without introducing nondeterministic LLM behavior.

## Workload structure

`experiments/workloads/long_coding_traj.py` builds an exact-length deterministic
trajectory (default 64 calls; minimum 54). Every run starts from the same seeded
repository. The phases are:

| phase | behavior |
|---|---|
| exploration | list files, read source/tests/config, and exercise a missing-path lookup |
| feature + refactor | add `sub`, `pow2`, and `div`, then split operations into modules |
| API/config pass | update the facade, tests, README, API notes, and agent configuration |
| failing CI loop | write faulty `lib/formatting.py` at step 27, append a formatting test, and observe the expected failure at step 29 |
| derived + independent work | read the faulty formatter into `build/format-report.txt` at step 30; independently update `docs/CHANGELOG.md`, `config/feature.flags`, and `docs/attempt-1.md` |
| repair + cleanup | write the fixed formatter at step 38, add API/validation checks, exercise delete effects, and finish with pytest |
| long tail | deterministic inspect/write iterations fill the requested length without changing the fault location |

The recovery experiment executes the prefix through the independent edits,
invokes rollback at the faulty write, verifies the state, and then executes the
repair suffix. The expected pre-repair state is: the faulty formatter and its
derived report are absent, while all three independent files remain available.

## Reproduction

```bash
cd /home/pengpeng/agenttx
PYTHONPATH=src:. python3 -m pytest -q
PYTHONPATH=src:. python3 experiments/scripts/bench_long_trajectory.py \
  --length 64 --repeats 1
```

The benchmark uses the same trajectory for `bare`, `per_call_try`,
`shared_try`, `shared_checkpoint`, `agenttx_without_read_tracing`, and
`agenttx_full`. It writes `experiments/results/long_workload_matrix.{csv,json,md}`.
The generated matrix records both runtime and semantic recovery; it is not a
replacement for external BranchFS/Waypoint/Sandlock measurements that are
blocked by this VM.

## VM result (2026-08-07, one repeat)

| mode | wall mean (s) | ms/step | failures | host polluted | ledger steps | read effects |
|---|---:|---:|---:|:---:|---:|---:|
| bare | 3.081 | 48.137 | 2 | yes | — | — |
| per-call try | 17.145 | 267.891 | 34 | no | — | — |
| shared try | 16.427 | 256.675 | 34 | no | — | — |
| shared checkpoint | 20.389 | 318.572 | 2 | no | — | — |
| AgentTX without read tracing | 20.564 | 321.311 | 2 | no | 64 | 15 |
| AgentTX full | 27.994 | 437.399 | 2 | no | 64 | 1,051 |

The two expected failures are the deliberate missing architecture read and the
injected formatting CI failure. Per-call/shared try are continuity baselines;
their larger failure count is evidence that isolated opaque calls do not provide
the same cross-step state contract as the harness.

## Recovery result

| mode | host polluted before recovery | causal retention correct | check | targets |
|---|:---:|:---:|:---:|---|
| bare | yes | no | — | — |
| per-call try | no | no | — | — |
| shared try | no | no | — | whole-session discard |
| shared checkpoint | no | no | failed | whole-session rollback |
| AgentTX without read tracing | no | no | failed | `[27]` |
| AgentTX full | no | **yes** | passed | `[27, 29, 30]` |

Full AgentTX removes the faulty producer, the failed CI read, and the derived
report, while retaining the independent documentation/configuration edits.
Without read tracing, the derived report has no causal edge and remains. This is
the central semantic result; the higher full-tracing cost is expected and is not
presented as a speed advantage.

## Limitations

This is a deterministic synthetic multi-file workload, not an LLM-generated
repository-scale project. The numbers are from one Linux VM and one repeat, so
they are suitable as a reproducibility point rather than a final statistical
claim. The next evaluation step is to add real multi-package agents and report
p50/p95 across repeated runs, while keeping the same causal-retention oracle.