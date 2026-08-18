# x86 repeated comparison (2026-08-18)

This artifact records the completed repeated comparison used in the paper.
It is separate from the earlier ARM64/tiao2 run and from the 64-call coding
workload.

## Host and command

- Host: `pengpeng-ubuntu-01-1`, x86_64
- Kernel: Linux 5.4.0-216-generic
- CPU: AMD EPYC 7713, 32 online CPUs
- Memory: 62 GiB
- Python: 3.8.10
- AgentTX commit at launch: `effb118f1898a0e5eadc80a82f1ea972e11e3dcc`
- Trajectory: ten independent `echo >> out.txt` writes
- Samples: 50 fresh workspaces per mode
- Full tracing backend: `strace` (selected explicitly)

The run used the privileged OverlayFS profile required by this host:

```bash
sudo env TRY_SKIP_USERNS=1 \
  PYTHONPATH=/home/pengpeng/agenttx/src:/home/pengpeng/agenttx \
  python3 -u experiments/scripts/bench_comparison_repeats.py \
  --repeats 50 --n 10
```

## Runtime distribution

| mode | mean ms/step | p50 | p95 | p99 | samples |
|---|---:|---:|---:|---:|---:|
| bare | 2.164 | 2.095 | 2.323 | 4.976 | 50 |
| per-call try | 193.726 | 193.826 | 201.427 | 202.746 | 50 |
| session try | 18.621 | 18.232 | 21.988 | 22.390 | 50 |
| shared try | 184.230 | 184.204 | 190.712 | 196.776 | 50 |
| shared checkpoint | 47.186 | 46.599 | 51.206 | 52.147 | 50 |
| bubblewrap | 1.145 | 1.162 | 1.274 | 1.301 | 50 |
| AgentTX no-trace | 50.395 | 49.818 | 54.100 | 55.508 | 50 |
| AgentTX full | 58.757 | 57.738 | 63.511 | 68.169 | 50 |

## Recovery predicate

The predicate requires a clean host before recovery, removal of the invalid
producer and dependent artifact, and retention of the independent artifact.
All modes were supported.  Bare and every baseline except AgentTX full scored
0/50; AgentTX full scored 50/50.  The raw files are
`experiments/results/comparison_repeats.{csv,json,md}` on the remote host.

The repeated comparison is a distribution check for the fixed ten-write
trajectory.  It does not replace the 64-call coding workload, the causal DAG
sweep, or the real-agent/token experiments.
