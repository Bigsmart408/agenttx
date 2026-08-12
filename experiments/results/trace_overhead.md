# Automatic dependency-tracing overhead

No-op tool calls; 20 steps per run, 3 repeats.

| mode | per_step_ms_mean | per_step_ms_stdev |
|---|---:|---:|
| trace_off | 18.52 | 0.52 |
| trace_on | 27.30 | 0.28 |

Incremental tracing cost: 8.78 ms/step (47.4%).
