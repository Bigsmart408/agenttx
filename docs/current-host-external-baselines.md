# Current-host external baseline deployment (updated 2026-09-22)

This record covers artifact-native deployment on `r760-5th-1`. It supersedes
the external-baseline blocker for this host, but does not rewrite the historical
ARM64/tiao2 record in `docs/tiao2-comparison-run.md`.

## Host and toolchain

- Host: `r760-5th-1`, x86_64, Ubuntu 20.04.6 LTS.
- Kernel: Linux 6.15.7.
- CPU: Intel Xeon Gold 6530; memory: 125 GiB.
- Filesystem: ext4 on an Intel NVMe SSD.
- Rust: 1.98.0, installed with the minimal rustup profile.
- FUSE: libfuse3/fuse3 3.9.0; `/dev/fuse` is user-accessible.

## Pinned external artifacts

| System | Upstream | Commit | Installed artifact |
|---|---|---|---|
| BranchFS | `https://github.com/multikernel/branchfs.git` | `a4b6592d31aacb4d2f94d0c80f43d47d38063fa9` | `/usr/local/bin/branchfs` |
| YoloFS | `https://github.com/YoloFS/YoloFS.git` | `77fd8966e90c61c739b00920da8cb295279c7678` | `/usr/local/bin/yolo`, `/lib/modules/6.15.7/extra/yolofs.ko` |
| YoloFS perf harness | `https://github.com/YoloFS/perf-eval.git` | `bc32d0835ed8b9d28a799e59574f31855999a89a` | `third_party/yolofs-perf-eval/target/release/yolo-bench` |
| try | `https://github.com/binpash/try.git` | `60fa324c37158469c6e0f97718d1e479f9355127` | `third_party/try/try` |
| Sandlock | `https://github.com/multikernel/sandlock.git` | `c31ff339cce3c24d2eb17ea10ff69f9aa8a77fc0` | `third_party/sandlock/target/release/sandlock` |
| Waypoint | `https://github.com/Alex-XJK/waypoint.git` | `dcb6a7b0f4114f4732b9d7f1d1824dc5e4ff4a24` | privileged evaluation container, CRIU 4.2.1 |

The perf harness has one local plumbing change: its `yolofs` path dependency
points to `../yolofs` rather than the upstream checkout name `../filesystem`.
No benchmark or filesystem behavior was changed.

The BranchFS benchmark has one local logging-plumbing fix. The upstream script
tries to capture `[BENCH]` records from `branchfs mount`, while that command
starts a detached daemon whose stdout/stderr are redirected to `/dev/null`.
The patched launcher starts BranchFS's own hidden `run-daemon` entrypoint in the
foreground and then issues the normal mount request. Workloads, parameter steps,
timing sites, iteration counts, and filesystem code are unchanged.

## Build and validation

BranchFS builds with `cargo build --release`. Its 14 Rust unit tests pass, and
all 13 suites in `tests/run_all_tests.sh` pass with real FUSE mounts, including
branch isolation, nested branches, commit, abort, rename, symlink, quota, and
ioctl coverage.

YoloFS builds both its release CLI and a module for the running kernel. All 295
userspace unit tests pass. The module loads successfully and a manual mounted
workflow verifies staging, commit, remount, and abort. The upstream e2e suite
passes 585/586 tests. The remaining test,
`cli::test_unmount::unmount_reports_blocking_process`, fails reproducibly because
the second unmount still reports the killed `sleep` process as a blocker. This
is recorded as a host/upstream compatibility issue; it does not prevent normal
mount, staging, commit, abort, or benchmark execution.

## First common-harness measurement

The upstream YoloFS harness ran `write-files` over native, both YoloFS profiles,
and BranchFS. The workload creates 1,000 4-KiB files. Each backend received one
warm-up and three timed iterations.

| Backend | Timed totals (ms) | Mean total (ms) |
|---|---|---:|
| native | 132, 137, 136 | 135.0 |
| yolo-no-perm | 304, 311, 285 | 300.0 |
| yolo-realistic | 287, 296, 298 | 293.7 |
| branchfs | 2043, 1941, 2059 | 2014.3 |

The archived raw output is in
`experiments/results/external_yolofs_branchfs_write_files_20260903.json`
(the harness-local copy remains under `third_party/perf-results/`). BranchFS's
upstream quick benchmark is in
`experiments/results/branchfs_upstream_quick.json`; its throughput and nested
read cells ran, while its internal create/commit/abort log parser found no
timing records at this commit.

These values validate artifact-native deployment and a shared upstream harness.
They do not replace AgentTX's paper scaling table, which uses a different
32/64/96/128-call mixed trajectory. A common AgentTX/BranchFS/YoloFS trajectory
adapter and repeated run are still required before changing that table.

## BranchFS upstream scaling measurements

The full upstream `bench/branchfs_bench.py` suite was run with its default
parameters. Creation uses five iterations per point, commit and abort use three,
and nested reads use 50. Values below are arithmetic means reported by the
upstream harness.

| Visible base files | Branch creation (ms) |
|---:|---:|
| 100 | 5.856 |
| 1,000 | 26.528 |
| 10,000 | 199.466 |

| Modified data (KiB) | Commit (ms) | Abort (ms) |
|---:|---:|---:|
| 1 | 2.929 | 2.732 |
| 10 | 3.149 | - |
| 100 | 4.043 | 2.769 |
| 1,000 | 4.970 | 1.909 |

| Nested depth | Read latency (us) |
|---:|---:|
| 1 | 223.1 |
| 2 | 242.3 |
| 4 | 180.4 |
| 8 | 176.1 |

For the upstream 50-MiB, 64-KiB-block throughput point, BranchFS measured
1,625.7 MB/s read and 858.6 MB/s write. The complete 16-record output is in
`experiments/results/branchfs_upstream_full_steps_20260903.json`.

## Still unavailable

- DeltaBox explicitly states that its kernel patch, controller, and benchmark
  scripts are not public.
- No official Crab implementation or artifact was found with the paper.

Neither system can be represented by local proxy measurements under its product
name.

## Baseline selection and reproducibility status (2026-09-22)

Table 1 in the paper classifies native recovery semantics; it is not an
artifact-availability table. The experiment set uses the following stricter
rule: a result is labeled with a system's name only when that system's public
artifact executes its native recovery operation.

| System | Public artifact | Native comparison operation | Current-host status | Experiment role |
|---|---|---|---|---|
| try | MIT | discard one command effect set, or discard a session layer | pinned and runnable | command/session isolation baseline |
| YoloFS | GPL-2.0 | `snapshot` followed by `travel` | pinned, mounted, and tested | temporal session recovery baseline |
| BranchFS | MIT | abort the active leaf branch | pinned, mounted, and tested | whole-branch recovery baseline |
| Waypoint | Apache-2.0 | fork from an immutable process/filesystem checkpoint | runnable in a privileged, network-disabled container with CRIU 4.2.1 and host-ext4 runtime storage | full-state temporal recovery baseline; container latency reported separately |
| Sandlock | Apache-2.0 | commit or discard one sandboxed process run | builds; blocked because this kernel has `CONFIG_SECURITY_LANDLOCK` unset | confinement/overhead ablation on a Landlock-enabled host |
| Crab | none located | restore a selected sandbox checkpoint | unavailable | qualitative comparison only |
| DeltaBox | not released by authors | restore a sandbox checkpoint | unavailable | qualitative comparison only |

The immediate artifact-native recovery comparison on this host therefore
contains try, YoloFS, BranchFS, Waypoint, and AgentTX. Waypoint's official demo
passes all 150 recovery, checkpoint, fork, isolation, and cleanup checks; its
sole failing assertion checks that `/dev/ptmx` is a character device, whereas
Docker intentionally exposes it as a `pts/ptmx` symlink. This environment-only
assertion is recorded separately. Sandlock should be added on a host whose
kernel enables Landlock. Sandlock is useful for measuring the cost of unprivileged confinement,
but its whole-process commit/abort semantics do not make it a replacement for a
trajectory recovery baseline.

Sandlock 0.8.8 builds successfully from the pinned commit. Its core test suite
passes 820 of 821 tests; the remaining test and both commit/abort CLI smoke runs
fail before executing the workload because the current kernel configuration has
`CONFIG_SECURITY_LANDLOCK` unset. `/sys/kernel/security/lsm` likewise omits
Landlock. No Sandlock latency or recovery result from this host is valid. The
machine-readable preflight record is
`experiments/results/external_sandlock_preflight_20260922.json`.

## Common recovery experiment contract

All systems receive the same multi-turn manifest, initial workspace, fault
location, and crash notification. The workload records the failed operation,
its dependent operations, and later independent work. Each baseline then uses
only its native recovery boundary:

1. try discards the affected command layer or, for the session configuration,
   the session layer.
2. YoloFS travels to the snapshot immediately preceding the affected turn.
3. BranchFS aborts the leaf branch containing the failure.
4. Waypoint forks from the checkpoint immediately preceding the affected turn.
5. AgentTX searches its recorded dependencies and removes the failed operation
   and the operations that depend on it.

The primary correctness metrics are removal of invalid state and retention of
independent valid state. Performance metrics are recovery latency, end-to-end
latency, storage amplification, and setup cost. The harness must also report
privilege and kernel requirements. It must not add AgentTX's dependency
scheduler to an external system and then attribute the resulting selectivity to
that system.

The artifact-native driver is
`experiments/scripts/bench_external_recovery_artifacts.py`. Its paper mode
reconstructs and hash-checks the complete 400-manifest Motivation population
(100 MB2 + 90 MB3 + 210 MB4), executes all five runnable backends, and validates
each recovered workspace against one shared oracle. `--resume` reuses only rows
matching backend, suite slice, trace ID, and manifest hash. Sandlock remains a
second-stage adapter because it requires a Landlock-enabled kernel.
