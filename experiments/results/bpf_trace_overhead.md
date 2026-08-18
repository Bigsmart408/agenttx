# eBPF vs strace dependency-tracing overhead

read workload; 12 steps per run, 2 repeats.

| mode | per_step_ms_mean | per_step_ms_p50 | per_step_ms_p95 |
|---|---:|---:|---:|
| off | 17.70 | 2.99 | 178.93 |
| strace | 25.60 | 11.67 | 183.29 |
| bpf | 61.39 | 26.29 | 413.54 |

strace incremental cost: 7.90 ms/step (44.6%).
Persistent eBPF incremental cost: 43.69 ms/step (246.8%).

Capture verification: every read step yielded both the `input.txt` READ and the `missing.txt` NEGATIVE effect for all traced modes.
