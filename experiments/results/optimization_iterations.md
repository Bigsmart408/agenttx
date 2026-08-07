# Optimization iteration measurements

These are paired before/after records for the source snapshots under `src/agenttx/optimization_history/`.

| iteration | snapshot | optimization | before full ms/step | after full ms/step | after repeats | after stdev | correctness | note |
|---:|---|---|---:|---:|---:|---:|:---:|---|
| 0 | `iteration_00_unoptimized` | known write/delete trace bypass | 495.843 | 500.906 | 1 |  | True | directional single-run post-change measurement |
| 1 | `iteration_01_known_write_trace_bypass` | known read_file explicit READ/NEGATIVE effects + trace bypass | 500.906 | 437.534 | 2 | 10.589 | True | paired current implementation; full recovery passed; no-trace ablation remained intentionally incorrect |
| 2 | `iteration_02_known_read_effect_bypass` | persistent per-semisolate command script reuse | 418.899 | 409.835 | 2 | 6.003 | True | directional two-repeat measurement; no-trace 331.599 -> 328.601 ms/step; full recovery passed; ablation remained intentionally incorrect |
| 3 | `iteration_03_persistent_command_script` | defer unreachable blob GC from per-snapshot hot path | 406.099 | 397.104 | 2 | 0.891 | True | directional two-repeat measurement; no-trace 325.434 -> 324.325 ms/step; full recovery and evidence suite passed |
| 4 | `iteration_04_deferred_blob_gc` | execute reusable command script directly via shebang | 397.104 | 393.631 | 2 | 1.286 | True | directional two-repeat measurement; no-trace 324.325 -> 318.575 ms/step; full recovery and evidence suite passed |
| 5 | `iteration_05_direct_executable_script` | persistent try worker with framed command IPC | 393.631 | 151.531 | 2 | 5.013 | True | directional two-repeat measurement; no-trace 318.575 -> 66.975 ms/step; full recovery and evidence suite passed; worker fallback retained |

Iteration 05 is a directional two-repeat measurement. The before pair was collected immediately before the change and the after pair immediately after it, but the VM was not interleaved; use the values for engineering guidance until an interleaved run is collected.
