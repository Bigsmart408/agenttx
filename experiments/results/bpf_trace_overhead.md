# eBPF vs strace dependency-tracing overhead

read workload; 20 steps per run, 3 repeats.

| mode | per_step_ms_mean | per_step_ms_p50 | per_step_ms_p95 |
|---|---:|---:|---:|
| off | 11.48 | 2.44 | 168.98 |
| strace | 18.89 | 10.07 | 183.10 |
| bpf | 1376.71 | 1372.21 | 1555.59 |

strace incremental cost: 7.41 ms/step (64.5%).
eBPF incremental cost: 1365.23 ms/step (11890.1%).

Capture verification: every read step yielded both the `input.txt` READ and the `missing.txt` NEGATIVE effect for both traced modes.
