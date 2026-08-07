# AgentTX optimization history

The production hot path is optimized iteratively. Before each optimization, the
previous implementation is copied into this source directory so the motivation
and cost of each change remain auditable. These snapshots are not imported at
runtime. Trailing whitespace is normalized for repository hygiene; executable code and behavior are preserved.

| iteration | pre-optimization snapshot | change that follows | before -> after (64-call full) |
|---:|---|---|---:|
| 00 | `iteration_00_unoptimized` | bypass tracing for known write/delete harness tools | 495.843 -> 500.906 ms/step* |
| 01 | `iteration_01_known_write_trace_bypass` | explicit READ/NEGATIVE effects for known `read_file`, then bypass its strace | 500.906 -> 437.534 ms/step* |

The first optimization is intentionally conservative: opaque `run_shell` and
`run_tests` still use full syscall tracing. The second optimization preserves
negative lookup semantics through `AgentTX.path_exists()` and explicit ledger
effects. Full causal recovery remains correct; the no-trace ablation still
retains the derived report as expected.

*The 495.843 ms/step baseline uses two repeats; the 437.534 ms/step value uses two
current repeats, while the intermediate 500.906 ms/step point is a single run. A
controlled interleaved before/after run is still needed for a final statistical claim. Future iterations must add another `iteration_NN_*`
snapshot before modifying production files, record the old/new metrics, and run
the full correctness suite.