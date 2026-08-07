# Automatic dependency-tracing overhead

No-op tool calls; 20 steps per run, 3 repeats.

| mode | per_step_ms_mean | per_step_ms_stdev |
|---|---:|---:|
| trace_off | 295.63 | 3.20 |
| trace_on | 319.43 | 5.00 |

Incremental tracing cost: 23.80 ms/step (8.0%).
