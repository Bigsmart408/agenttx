# Comparison repeats manifest

- timestamp: 2026-08-18 (remote host time)
- host: `pengpeng-ubuntu-01-1`
- architecture: `x86_64`
- kernel: `Linux 5.4.0-216-generic #236-Ubuntu SMP Fri Apr 11 19:53:21 UTC 2025`
- CPU: `AMD EPYC 7713 64-Core Processor`, 32 online CPUs
- memory: 62 GiB
- Python: 3.8.10
- AgentTX commit: `effb118f1898a0e5eadc80a82f1ea972e11e3dcc`
- trajectory: 10 writes (`echo i >> out.txt` for `i=0..9`)
- repeats: 50 independent fresh workspaces per mode
- modes: bare, per-call try, session try, shared try, shared checkpoint,
  bubblewrap, AgentTX no-trace, AgentTX full
- full trace backend: `strace`, selected explicitly
- isolation profile: `TRY_SKIP_USERNS=1` with privileged OverlayFS
- command: `sudo env TRY_SKIP_USERNS=1 PYTHONPATH=/home/pengpeng/agenttx/src:/home/pengpeng/agenttx python3 -u experiments/scripts/bench_comparison_repeats.py --repeats 50 --n 10`
- exit status: 0
- raw artifacts: `experiments/results/comparison_repeats.{csv,json,md}`
- plots: `motivation/FIG-Comparison-{Mean,P50,P95,P99,Repeats}.{pdf,png}`

The mode order is the deterministic order in `bench_comparison_repeats.py`;
the experiment does not claim randomized scheduling.  Recovery correctness is
checked independently from runtime latency on each fresh workspace.
