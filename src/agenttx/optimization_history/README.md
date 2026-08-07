# AgentTX optimization history

The production hot path is optimized iteratively. Before each optimization, the
previous implementation is copied into this source directory so the motivation
and cost of each change remain auditable. These snapshots are not imported at
runtime. Trailing whitespace is normalized for repository hygiene; executable code and
behavior are preserved.

| iteration | pre-optimization snapshot | change that follows | before -> after (64-call full) |
|---:|---|---|---:|
| 00 | `iteration_00_unoptimized` | bypass tracing for known write/delete harness tools | 495.843 -> 500.906 ms/step* |
| 01 | `iteration_01_known_write_trace_bypass` | explicit READ/NEGATIVE effects for known `read_file`, then bypass its strace | 500.906 -> 437.534 ms/step* |
| 02 | `iteration_02_known_read_effect_bypass` | reuse one private command script per semisolate; clean it at close | 418.899 -> 409.835 ms/step* |
| 03 | `iteration_03_persistent_command_script` | defer unreachable blob GC from every snapshot to rollback/retained-session cleanup | 406.099 -> 397.104 ms/step* |
| 04 | `iteration_04_deferred_blob_gc` | execute the reusable command script directly through its `/bin/bash` shebang | 397.104 -> 393.631 ms/step* |
| 05 | `iteration_05_direct_executable_script` | persistent try worker: one namespace/overlay per session with framed command IPC and safe fallback | 393.631 -> 151.531 ms/step* |
| 06 | `iteration_06_persistent_try_worker` | incremental upperdir snapshot: clone the prior snapshot and replay only changed paths | snapshot stage 0.384 -> 0.158 s* |
| 07 | `iteration_07_robustness_evaluation` | evaluation hooks and workloads for p50/p95 tails, worker crash recovery, long sessions, and concurrent agents | correctness/robustness artifact* |

The first optimization is intentionally conservative: opaque `run_shell` and
`run_tests` still use full syscall tracing. The second optimization preserves
negative lookup semantics through `AgentTX.path_exists()` and explicit ledger
effects. The third optimization only changes temporary command-script lifecycle;
commands still execute through the same try wrapper and close-time cleanup. The
fourth optimization only changes when unreachable content blobs are scanned and
removed; rollback still performs GC before returning. The fifth optimization removes one redundant shell parse while preserving the
same script body. The sixth optimization keeps one try namespace/overlay alive
per session and stops it at rollback/commit/reset boundaries; a worker failure
falls back to the original per-call path. Full causal recovery remains correct;
the no-trace ablation still retains the derived report as expected. The seventh
optimization clones the previous upperdir snapshot with hard links for unchanged
entries and replays only the current step's write/delete paths. Commit, rollback,
reset, and resume boundaries deliberately request a full snapshot for safety.

*Iteration 06 is reported with a snapshot-stage metric rather than an endpoint
speedup claim: a controlled 64-call profile reduced cumulative `snapshot_before`
time from 0.384 s to 0.158 s over 63 incremental calls. The paired end-to-end
full means (151.531 ms/step before, 162.104 ms/step after) were noisy on the VM,
so no total-latency improvement is claimed.*

Iteration 07 adds no normal-path optimization; its source snapshot records the
pre-hook runtime so fault injection and robustness measurements remain auditable.

*The 495.843 ms/step baseline uses two repeats; the 437.534 ms/step value uses two
current repeats, while the intermediate 500.906 ms/step point is a single run. A
controlled interleaved before/after run is still needed for a final statistical claim. Future iterations must add another `iteration_NN_*`
snapshot before modifying production files, record the old/new metrics, and run
the full correctness suite.
