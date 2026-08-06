# Automatic dependency-tracing overhead

No-op tool calls; 10 steps per run, 3 repeats.

| mode | per_step_ms_mean | per_step_ms_stdev |
|---|---:|---:|
| trace_off | 184.35 | 7.57 |
| trace_on | 202.26 | 7.29 |

Incremental tracing cost: 17.91 ms/step (9.7%).
