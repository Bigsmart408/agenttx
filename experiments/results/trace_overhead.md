# Automatic dependency-tracing overhead

No-op tool calls; 20 steps per run, 3 repeats.

| mode | per_step_ms_mean | per_step_ms_stdev |
|---|---:|---:|
| trace_off | 110.19 | 2.36 |
| trace_on | 118.07 | 2.21 |

Incremental tracing cost: 7.88 ms/step (7.2%).
