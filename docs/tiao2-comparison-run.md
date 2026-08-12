# tiao2 comparison run (2026-08-11)

This note records the reproducible comparison run performed on the remote
`tiao2` host.  The result files under `experiments/results/` are the raw
artifacts for this run; the numbers below are intentionally host-local.

## Host and execution profile

- Host: `pengpeng-ubuntu-01`, `aarch64` (ARM64), Linux `5.4.0-216-generic`.
- Python: Miniforge environment `kitemguard311`, Python 3.11.15.
- Tracing: `strace` 5.5.
- Isolation: upstream `try` (commit `60fa324`) plus `fuse-overlayfs`/`unionfs-fuse`
  packages.  The kernel rejects the upstream unprivileged OverlayFS mount on
  this VM.  For measurements only, the vendored `try` copy has a root profile
  selected by `TRY_SKIP_USERNS=1`: it keeps mount and PID namespaces, skips
  `userxattr`, and runs the overlay mount with root capabilities.  The default
  unprivileged path is unchanged.
- Bubblewrap: 0.4.0, now installed and measured as a whole-session namespace
  lower bound.
- Validation: `76 passed` with the same Python/PATH profile used by the
  experiments.

The PATH setting is part of the profile.  Without it, commands inside the
overlay resolve the system Python 3.8 and the seeded pytest workloads fail for
an environmental reason rather than a workload reason.

## Primary comparison matrix

Command:

```bash
sudo env TRY_SKIP_USERNS=1 \
  PATH=/home/pengpeng/miniforge3/envs/kitemguard311/bin:$PATH \
  PYTHONPATH=$PWD/src:$PWD \
  /home/pengpeng/miniforge3/envs/kitemguard311/bin/python \
  experiments/scripts/bench_comparison_matrix.py --repeats 3 --n 10
```

| mode | per-step ms | recovery result |
|---|---:|---|
| bare | 1.641 | host writes visible; whole cleanup loses `c.txt` |
| per-call `try` | 239.072 | calls cannot pass `a.txt` to the next call |
| session `try` | 23.411 | shared state, but only whole-session abort |
| shared `try` | 235.546 | shared upperdir, but no causal retention |
| shared checkpoint | 36.643 | whole checkpoint rollback removes independent `c.txt` |
| bubblewrap | 0.848 | whole-session namespace abort; no causal retention |
| AgentTX, tracing off | 39.913 | derived `b.txt` is retained after rollback |
| AgentTX, full | 48.815 | removes `a.txt` and dependent `b.txt`, retains `c.txt` |

The full AgentTX recovery row is the only row with `causal retention correct =
True`.  Bubblewrap's low number is not a causal-recovery speed claim: its
benchmark executes the complete trajectory in one namespace and does not
provide per-tool commit/rollback semantics.

## 64-step Agent workload

The deterministic workload contains exploration, a modular refactor, a failing
CI loop, independent docs/config edits, and repair.  The full mode costs
`152.198 ms/step` on this host (`9.741 s` total), compared with `240.014
ms/step` for per-call `try` and `77.316 ms/step` for shared checkpoint.  The
causal recovery targets are `[27, 29, 30]`; read tracing off only finds `[27]`
and leaves the derived report behind.

The scaling sweep (two repeats per point) gives full-mode means of 177.049,
153.563, and 115.140 ms/step for 54, 64, and 96 calls respectively.  The
decrease is expected for this deterministic workload because the fixed setup
and test phases are amortized; it is not a claim of sublinear execution.

## Causal-retention and robustness refresh

- At 64 DAG nodes, causal recovery retains 100% of independent effects and
  removes 100% of invalid effects.  Temporal checkpoint retains 41.0%; whole
  session retains 0%; dependency tracing disabled removes only 4.0% of invalid
  effects.
- Trace overhead on 20 no-op calls is 18.52 ms/step off versus 27.30 ms/step
  on, an incremental 8.78 ms/step (47.4%).
- Content-addressed snapshots use 9,109,504 physical bytes for 100,663,296
  logical bytes (ratio 0.090).
- Robustness: full-mode p50/p95 call latency is 24.902/812.575 ms; the
  injected worker crash succeeds through one-shot fallback; a 256-step session
  reloads at step 128; four concurrent disjoint agents complete without cross
  contamination.

## External baseline probe

BranchFS was cloned at `a4b6592` and its ARM64 build was attempted.  The host
Cargo is 1.75; the latest dependency graph requires edition 2024, so a local
compatibility attempt pinned `clap=4.5.20`, `uuid=1.8.0`, and `fuser=0.14.0`.
That build still fails because the BranchFS source uses the newer fuser API
(`BackingId`, `FUSE_PASSTHROUGH`, `open_backing`, and the newer `getattr`
signature).  A modern Rust toolchain download timed out on this host.  No
BranchFS performance number is reported.

Waypoint requires CRIU; Ubuntu focal ARM repositories expose only Go bindings,
not the CRIU executable, so it was not run.  DeltaBox, YoloFS, Crab, Sandlock,
and Cordon remain artifact- or environment-blocked as documented in
`docs/related-work-2026.md`.  These are explicit blockers, not zero-valued
baselines.

## Source artifacts

- `experiments/results/comparison_matrix.{csv,json,md}`
- `experiments/results/long_workload_matrix.{csv,json,md}`
- `experiments/results/long_workload_scaling.{csv,json,md}`
- `experiments/results/causal_retention.{csv,json,md}`
- `experiments/results/trace_overhead.{csv,md}`
- `experiments/results/snapshot_storage.{csv,md}`
- `experiments/results/robustness.{csv,json,md}`

## LLM-dependent experiments

The rerun attempted `bench_real_agent_recovery.py`, `bench_real_agent.py`, and
`bench_token_recovery.py`.  Each script exited without changing its prior
artifact because no `OPENAI_API_KEY` or `OPENROUTER_API_KEY` was visible in the
sudo experiment environment.  The corresponding old figures are therefore not
presented as results of this rerun; provide a key in that environment before
refreshing them.

## Repeated comparison refresh

The companion `experiments/scripts/bench_comparison_repeats.py` ran every
supported baseline fifty times on fresh workspaces. It reports runtime mean,
standard deviation, p50, p95, p99, and a fifty-sample causal-correctness rate without
overwriting the three-repeat `comparison_matrix.*` artifacts. The current
summary is in `experiments/results/comparison_repeats.{csv,json,md}`; AgentTX
full is 49.689 ms/step mean (p50 49.599, p95 50.900, p99 51.146), while
per-call `try` is 245.365 ms/step mean (p95 250.565, p99 252.112). AgentTX
full is causally correct in 50/50 fresh workspaces; all other comparison
policies are 0/50.
