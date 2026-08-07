# Optimization iteration measurements

These are paired before/after records for the source snapshots under `src/agenttx/optimization_history/`.

| iteration | snapshot | optimization | before full ms/step | after full ms/step | after repeats | after stdev | correctness | note |
|---:|---|---|---:|---:|---:|---:|:---:|---|
| 0 | `iteration_00_unoptimized` | known write/delete trace bypass | 495.843 | 500.906 | 1 |  | True | directional single-run post-change measurement |
| 1 | `iteration_01_known_write_trace_bypass` | known read_file explicit READ/NEGATIVE effects + trace bypass | 500.906 | 437.534 | 2 | 10.589 | True | paired current implementation; full recovery passed; no-trace ablation remained intentionally incorrect |

The iteration 01 result is a two-repeat paired measurement of the current implementation; the earlier baseline and iteration 00 post-change point were collected at different VM times, so the improvement is directional until a controlled interleaved run is added.
