# Step 18 — optimization iteration history and hot-path changes

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
| AgentTX without read tracing | 385.294 ms/step | 385.294 ms/step | expected ablation failure |
| AgentTX full | 500.906 ms/step | 437.534 ms/step | passed |

The full run recorded the same causal targets `[27, 29, 30]` and retained the
independent docs/config edits. The no-trace ablation continued to retain the
derived report, as expected. The current full mean is roughly 12.7% below the single-run previous value, with 10.589 ms/step standard deviation across two repeats. The older baseline was not interleaved with this run, so a controlled paired run is still needed for a final claim.

## Iteration 02 ? current: persistent command script

The pre-image for this change is `iteration_02_known_read_effect_bypass`. The
previous implementation created a fresh `/tmp/agenttx-cmd-*` directory, wrote and
chmod-ed `cmd.sh`, then recursively deleted that directory after every tool call.
The current implementation allocates one private command directory per
`SharedSemisolate`, rewrites the same script each step, and removes it exactly
once in `close()`. The try command, shell body, tracing policy, and effect
collection are unchanged. A focused lifecycle test checks both path reuse and
close-time cleanup.

The controlled 64-call measurements were:

| mode | before | current | delta | recovery |
|---|---:|---:|---:|:---:|
| AgentTX without read tracing | 331.599 ms/step | 328.601 ms/step | -0.9% | expected ablation failure |
| AgentTX full | 418.899 ms/step | 409.835 ms/step | -2.2% | passed |

The full after standard deviation was 6.003 ms/step across two repeats (before
standard deviation 0.344 ms/step), so this is retained as a low-risk directional
optimization, not a final OSDI performance claim.

## Iteration 03 ? current: deferred blob garbage collection

The pre-image for this change is `iteration_03_persistent_command_script`. Every
`LayerStore.snapshot_before()` call used to scan the content-addressed blob store
and remove unreferenced blobs. During a trajectory, retained snapshots cannot
lose those blobs, so the scan is unnecessary in the hot path. The current code
defers the scan until rollback (where snapshots are dropped) or an explicit
retained-session close; destroying a session still removes the whole tree.

The controlled 64-call measurements were:

| mode | before | current | delta | recovery |
|---|---:|---:|---:|:---:|
| AgentTX without read tracing | 325.434 ms/step | 324.325 ms/step | -0.3% | expected ablation failure |
| AgentTX full | 406.099 ms/step | 397.104 ms/step | -2.2% | passed |

The full after standard deviation was 0.891 ms/step across two repeats. The
change is retained as a low-risk directional optimization; it does not claim a
final statistical improvement until an interleaved run is collected.

## Iteration 04 ? current: direct executable command scripts

The pre-image for this change is `iteration_04_deferred_blob_gc`. AgentTX already
kept one executable command script per semisolate, but invoked it as
`bash /tmp/.../cmd.sh`, causing an extra shell parse for every try call. The
current script uses a fixed `/bin/bash` shebang and executable mode, so the try
command executes the script path directly. The command body, quoting fallback,
trace placement, and cleanup lifecycle are unchanged.

The controlled 64-call measurements were:

| mode | before | current | delta | recovery |
|---|---:|---:|---:|:---:|
| AgentTX without read tracing | 324.325 ms/step | 318.575 ms/step | -1.8% | expected ablation failure |
| AgentTX full | 397.104 ms/step | 393.631 ms/step | -0.9% | passed |

The full after standard deviation was 1.286 ms/step across two repeats. This is
retained as a low-risk directional optimization; an interleaved run is still
needed before making a final performance claim.

## Next optimization candidates

The source history now supports a clean before/after experiment for the higher-
risk changes: incremental upperdir digests/snapshots, journaled ledger
persistence, and a persistent try/worker session. Those changes must preserve
rollback, crash recovery, and the full-trace causal oracle.

Artifacts:

- `src/agenttx/optimization_history/README.md`
- `src/agenttx/optimization_history/iteration_00_unoptimized/`
- `src/agenttx/optimization_history/iteration_01_known_write_trace_bypass/`
- `src/agenttx/optimization_history/iteration_02_known_read_effect_bypass/`
- `src/agenttx/optimization_history/iteration_03_persistent_command_script/`
- `src/agenttx/optimization_history/iteration_04_deferred_blob_gc/`
- `experiments/results/long_workload_matrix.{csv,json,md}`
- `experiments/results/optimization_iterations.{csv,json,md}`