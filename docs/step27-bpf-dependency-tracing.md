# Step 27 - eBPF dependency tracing (strace replacement)

## Goal

`docs/step7-automatic-dependency-tracing.md` established strace-based capture
of workspace reads and negative lookups. This step replaces the ptrace
backend with an eBPF tracer: the same dependency semantics, captured from
kernel syscall tracepoints instead of a ptrace stop per syscall. The strace
backend stays fully supported; `auto` mode prefers eBPF when the host can
attach BPF programs and falls back to strace otherwise, preserving the
fail-closed guarantee of Step 7.

## Why eBPF

- **Capture point.** strace stops every traced process at every traced syscall
  (seccomp-accelerated on this kernel, but still a ptrace round trip with
  string copies). Tracepoints run in kernel context and only emit one record
  per syscall.
- **Filtering.** Syscall tracepoints are global, so every relevant syscall on
  the host is emitted; the userspace parser keeps only events whose pid
  belongs to the seed's descendant tree. The runtime snapshots that tree from
  `/proc` when the probes report `ATXBPF_READY`, and the parser extends it
  with `ATXBPF F` fork lines (strace `-f` equivalence). In-kernel fork
  tracking cannot work here: the try sandbox's setup forks happen *before*
  the probes attach, and `sys_exit_clone`'s retval is the pid in the caller's
  (nested) pid namespace, while bpftrace's `pid` builtin is the global pid.
- **Startup determinism.** The traced command is held on a release marker
  until bpftrace prints `ATXBPF_READY` (BEGIN fires only after all probes are
  attached), so no syscall of the command can escape the trace. The marker is
  a pre-created regular file in the overlay upperdir whose *content* the
  command polls (`hold` -> `go`). A FIFO cannot serve this handshake: FIFO
  pipe pairing does not cross the OverlayFS mount boundary (pipes are
  allocated against the superblock's user namespace), so a sandbox-side
  reader never pairs with a host-side writer — verified on this kernel in
  both directions. An existence poll would also race OverlayFS negative-
  dentry caching, so the command polls the content of a pre-existing file.

## Line protocol (`ATXBPF`)

The userspace parser pairs entry/exit events per tid, which is exact because a
thread executes one syscall at a time:

| Line | Fields |
|---|---|
| `ATXBPF_READY <seed>` | BEGIN marker; all probes attached |
| `ATXBPF E <pid> <tid> <call> <dfd> <flags> <path...>` | syscall entry; path is the final rest-of-line field |
| `ATXBPF X <pid> <tid> <call> <retval>` | syscall exit; retval is the negative errno on failure |
| `ATXBPF F <parent> <child>` | sched_process_fork; global pids extend the traced tree |
| `ATXBPF R <pid> <tid> <path...>` | kernel-resolved path for an in-flight open (optional) |

Paths are printed with plain `%s` because bpftrace 0.9 has no `%r` hex
specifier; the parser slices exactly the final field, so paths containing
spaces survive. `BPFTRACE_STRLEN` is raised from the 64-byte default to 4096
on bpftrace >= 0.10 (scratch-map strings); older builds keep strings on the
512-byte BPF stack and hard-fail above 200, so they get 200.

## Coverage and parity with the strace parser

The generated script covers the same syscall families as
`parse_strace_effects` (open/openat/openat2, stat/lstat/newfstatat/statx,
access/faccessat/faccessat2, readlink/readlinkat, execve/execveat,
chdir, statfs, listxattr/llistxattr/getxattr/lgetxattr, and
clone/clone3/fork/vfork for tree and cwd tracking). `parse_bpf_effects`
mirrors the strace parser's semantics exactly, including its classification
quirks (a non-ENOENT failed stat still proves a read attempt; a failed
write-open with ENOENT records a negative lookup).

Host-specific tracepoint names are resolved from tracefs when readable (root):
Ubuntu 5.4 exposes the `stat`/`lstat` syscalls as `sys_enter_newstat` /
`sys_enter_newlstat`, while other kernels use `sys_enter_stat` /
`sys_enter_lstat` — the attach pre-check retries both layouts and the emitted
events always carry the syscall name the parser understands. The probe list
is otherwise discovered per host (e.g. `openat2`/`faccessat2` do not exist on
5.4 and are dropped); a static default list covers kernels where tracefs is
unreadable, and legacy tracepoint field names (`path` vs `pathname`) are
retried when the modern layout fails to compile.

Two deliberate v0 limitations:

1. **Non-AT_FDCWD dirfds.** A relative path opened against a real dirfd is
   approximated against the process cwd until the resolved-path probe is
   available. On hosts with bpftrace >= 0.10 the generated script adds
   `kprobe:vfs_open` + `dpath()`, which reports the kernel-resolved path for
   every successful open (and restores the symlink-alias granularity that
   strace gets from `-yy`). The probe is pre-checked for attachability and
   silently dropped when unsupported; the parser handles both shapes.
2. **openat2 flags.** `open_how.flags` is read as the first u64 of the user
   struct, avoiding BTF-dependent struct definitions.

The attach pre-check (`bpf_attach_precheck`, run with `-c /bin/true`) is the
authoritative availability test, and an unattachable script fails there
rather than mid-step. The generated script uses one probe block per syscall
(never comma-joined), because bpftrace resolves `args` against the first
probe of a joined block and syscall tracepoints have heterogeneous structs;
separate blocks compile on every bpftrace version, 0.9.x included. The
`-q`/`--quiet` flag and the `BPFTRACE_STRLEN` ceiling are selected from the
installed bpftrace version (cached per session).

## Integration

- `SharedSemisolate(trace_backend="auto"|"strace"|"bpf")` selects the backend
  per session; `agenttx begin --trace-backend ...` exposes it on the CLI and
  the choice persists in `agenttx.json` and `status()`.
- Initialization fails closed: `bpf` requires a static root+bpftrace probe,
  `auto` requires at least one of strace or eBPF, exactly like Step 7's
  missing-strace behavior.
- The first traced step runs the attach pre-check once and caches the result;
  `auto` then uses eBPF if attachable, else strace.
- Both the persistent try worker and the one-shot path are supported: the
  worker polls the release marker before `subprocess.run` (a new optional
  `hold_marker` request field), the one-shot path runs a generated hold
  script (`wait for the marker, then exec`) — a script file rather than an
  inline `bash -c` string, because try word-splits inline arguments when it
  builds `script_to_execute.sh`.
- Readiness or log failures release the hold, let the command finish, and
  fail the step closed (the step stays speculative in the overlay; the host
  is never touched), matching the strace backend's missing-log behavior.
- `StepResult.tracer` records which backend produced a step's effects for
  reproducibility.

## Verification

Unit tests (`tests/test_bpf_trace.py`) cover parser parity with the strace
tests (read/write/negative discrimination, symlink aliases, chdir across
child processes, paths with spaces, malformed-line tolerance), the pid-tree
filter (unrelated host events dropped, sched_process_fork extension),
tracepoint filtering and stat-family aliasing, script generation, backend
resolution and fail-closed paths, the marker release protocol, and a full
orchestration run with a stub bpftrace and a fake try. The motivation
notebook `motivation/plot_bpf_trace.ipynb` plots the overhead comparison.

On a root-capable host, the end-to-end path is additionally exercised against
the real `try` overlay sandbox (which runs its commands in a private mount
and pid namespace): read and negative-lookup effects are captured through the
kernel tracepoints and matched against the `/proc` descendant snapshot.

## Reproducible benchmark

```bash
PYTHONPATH=src:. python3 experiments/scripts/bench_bpf_trace.py \
  --steps 20 --repeats 3 --workload read
```

writes `experiments/results/bpf_trace_overhead.{csv,json,md}` with mean/p50/p95
per-step cost for no tracing, strace, and eBPF, plus a capture-fidelity check
(every traced step must yield both the READ and the NEGATIVE effect; the
benchmark exits non-zero otherwise). Without root or bpftrace it exits
non-zero with a clear message and writes nothing.

## Measured results (this host)

Host: bpftrace 0.9.4, kernel 5.4.0-216, `read` workload, 20 steps x 3
repeats, persistent try worker. Numbers from
`experiments/results/bpf_trace_overhead.{csv,json,md}` (mean/p50/p95
per-step ms):

| mode | mean | p50 | p95 |
|---|---:|---:|---:|
| no tracing | 11.48 | 2.44 | 168.98 |
| strace | 18.89 | 10.07 | 183.10 |
| eBPF | 1376.71 | 1372.21 | 1555.59 |

- strace incremental cost: +7.4 ms/step (+64.5%).
- eBPF incremental cost: +1365.2 ms/step — dominated by bpftrace 0.9.4's
  per-step probe attach and teardown (a timed SIGINT shutdown of the
  40-probe script takes ~1.2 s on this kernel; SIGKILL is not meaningfully
  faster, so the cost is kernel-side BPF program teardown, not userspace
  work).
- Capture fidelity: 60/60 read steps captured both the `input.txt` READ
  and the `missing.txt` NEGATIVE effect for both traced modes (a transient
  drop of the command's fork event on a noisy host is retried once and
  counted in `step_retries`).

So on this host's bpftrace 0.9.4, the per-step eBPF attach/teardown tax
dominates short commands and there is **no endpoint win over strace for
short steps** — matching the original caveat that the comparison must be
measured before claiming one. The eBPF backend's value is the kernel-side
capture point (no ptrace stops, in-kernel event production); amortizing the
attach with a persistent tracer (one attach per session, step boundaries
marked in the log) is the natural follow-up and would change the measured
numbers qualitatively.

## Remaining boundary

The eBPF backend still requires root (bpf syscall) and bpftrace; `auto` mode
falls back to strace when either is missing. Non-AT_FDCWD dirfd resolution is
approximate without the `dpath()` kprobe, and every host syscall is emitted
to the perf ring buffer (the parser filters in userspace), so a very busy
host could drop events; the per-step bpftrace attach cost dominates short
commands. Kernel-level atomic commit and hard-link identity remain out of
scope, as documented in Steps 10 and 23.
