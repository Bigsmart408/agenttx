# Step 4 — Coding agent harness + commit policy + long trajectory

## What landed

- `CommitPolicy`: deny dangerous paths / require writes under workdir before commit
- `CodingAgentHarness`: tool-boundary API (`write_file`, `read_file`, `run_shell`, `run_tests`, ...)
- Synthetic coding trajectory workload (`experiments/workloads/coding_traj.py`, >=24 tools)
- Long-trajectory bench: bare / per_call_try / agenttx

## Run

```bash
cd /home/bfq/agenttx
PYTHONPATH=src python3 tests/test_policy.py
PYTHONPATH=src:. python3 experiments/scripts/demo_coding_agent.py
PYTHONPATH=src:. python3 experiments/scripts/bench_long_trajectory.py 2
```

## Note

This harness is the integration seam for a real agent (Aider/OpenHands/etc.):
swap the scripted trajectory for live tool calls into the same harness methods.
