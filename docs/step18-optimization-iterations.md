# Step 18 — optimization iteration history and first hot-path change

## Motivation and reproducibility rule

The performance gap is part of the systems motivation, so optimization cannot be
reported only as a final number. Before every production change, the complete
hot-path source is copied under:

```text
src/agenttx/optimization_history/iteration_NN_<name>/
```

Each snapshot contains the prior `runtime.py`, `semisolate.py`, `layers.py`, and
`harness.py`, plus a manifest with the previous benchmark. The snapshots are
source artifacts only and are not imported by AgentTX. This makes each iteration
replayable and prevents a faster implementation from erasing the original
motivation.

## Iteration 00 → 01: known write/delete tools

The unoptimized baseline started strace for every harness tool. The first change
allows `write_file`, `append_file`, and `delete_file` to opt out of read tracing;
overlay fingerprints still record their writes/deletes. Opaque `run_shell` and
`run_tests` remain fully traced.

The baseline was 495.843 ms/step for a 64-call full AgentTX run (two repeats).
A single post-change run measured 500.906 ms/step, so this change is correctness-
preserving but not yet a statistically demonstrated speedup. The result is kept
because it is the pre-image for the next iteration rather than silently removed.

## Iteration 01 → current: known read_file effects

The next change makes `read_file` explicit as either `READ` or `NEGATIVE` using the
merged host/upperdir view, then skips strace for that trusted tool. This preserves
negative-lookup dependencies while removing one strace process and parser pass.

The paired 64-call result was:

| mode | before (iteration 01) | current | recovery |
|---|---:|---:|:---:|
| AgentTX without read tracing | 385.438 ms/step | 397.041 ms/step | expected ablation failure |
| AgentTX full | 500.906 ms/step | 437.534 ms/step | passed |

The full run recorded the same causal targets `[27, 29, 30]` and retained the
independent docs/config edits. The no-trace ablation continued to retain the
derived report, as expected. The current full mean is roughly 12.7% below the single-run previous value, with 10.589 ms/step standard deviation across two repeats. The older baseline was not interleaved with this run, so a controlled paired run is still needed for a final claim.

## Next optimization candidates

The source history now supports a clean before/after experiment for the higher-
risk changes: incremental upperdir digests/snapshots, journaled ledger
persistence, and a persistent try/worker session. Those changes must preserve
rollback, crash recovery, and the full-trace causal oracle.

Artifacts:

- `src/agenttx/optimization_history/README.md`
- `src/agenttx/optimization_history/iteration_00_unoptimized/`
- `src/agenttx/optimization_history/iteration_01_known_write_trace_bypass/`
- `experiments/results/long_workload_matrix.{csv,json,md}`
- `experiments/results/optimization_iterations.{csv,json,md}`