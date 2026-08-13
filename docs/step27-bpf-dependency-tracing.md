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
  per syscall for the traced process tree.
- **Filtering.** The allowed process tree is seeded in-kernel: `@allowed[seed]`
  is set at BEGIN and every successful clone/fork/vfork of an allowed process
  extends it (strace `-f` equivalence), so unrelated host activity is never
  recorded.
- **Startup determinism.** The traced command is held on a FIFO until bpftrace
  prints `ATXBPF_READY` (BEGIN fires only after all probes are attached), so
  no syscall of the command can escape the trace.

## Line protocol (`ATXBPF`)

The userspace parser pairs entry/exit events per tid, which is exact because a
thread executes one syscall at a time:

| Line | Fields |
|---|---|
| `ATXBPF_READY <seed>` | BEGIN marker; all probes attached |
| `ATXBPF E <pid> <tid> <call> <dfd> <flags> <path...>` | syscall entry; path is the final rest-of-line field |
| `ATXBPF X <pid> <tid> <call> <retval>` | syscall exit; retval is the negative errno on failure |
| `ATXBPF R <pid> <tid> <path...>` | kernel-resolved path for an in-flight open (optional) |

Paths are printed with plain `%s` because bpftrace 0.9 has no `%r` hex
specifier; the parser slices exactly the final field, so paths containing
spaces survive. `BPFTRACE_STRLEN=4096` raises the `str()` size limit from the
64-byte default.

## Coverage and parity with the strace parser

The generated script covers the same syscall families as
`parse_strace_effects` (open/openat/openat2, stat/lstat/newfstatat/statx,
access/faccessat/faccessat2, readlink/readlinkat, execve/execveat,
chdir, statfs, listxattr/llistxattr/getxattr/lgetxattr, and
clone/clone3/fork/vfork for tree and cwd tracking). `parse_bpf_effects`
mirrors the strace parser's semantics exactly, including its classification
quirks (a non-ENOENT failed stat still proves a read attempt; a failed
write-open with ENOENT records a negative lookup).

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

The probe list is discovered from tracefs when readable and otherwise uses a
static default list; the attach pre-check (`bpf_attach_precheck`, run with
`-c /bin/true`) is the authoritative availability test, and an unattachable
script fails there rather than mid-step.

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
  worker blocks on the hold FIFO before `subprocess.run` (a new optional
  `hold_fifo` request field), the one-shot path wraps the command in
  `bash -c 'IFS= read -r _ < "$0"; shift; exec "$@"'`.
- Readiness or log failures release the hold, let the command finish, and
  fail the step closed (the step stays speculative in the overlay; the host
  is never touched), matching the strace backend's missing-log behavior.
- `StepResult.tracer` records which backend produced a step's effects for
  reproducibility.

## Verification

Unit tests (`tests/test_bpf_trace.py`) cover parser parity with the strace
tests (read/write/negative discrimination, symlink aliases, chdir across
child processes, paths with spaces, malformed-line tolerance), script
generation and tracepoint filtering, backend resolution and fail-closed
paths, the FIFO release protocol, and a full orchestration run with a stub
bpftrace and a fake try. The motivation notebook
`motivation/plot_bpf_trace.ipynb` plots the overhead comparison.

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

## Remaining boundary

The eBPF backend still requires root (bpf syscall) and bpftrace; `auto` mode
falls back to strace when either is missing. Non-AT_FDCWD dirfd resolution is
approximate without the `dpath()` kprobe, and the per-step bpftrace attach
cost on this host's bpftrace 0.9.4 is expected to dominate short commands,
so the overhead comparison must be measured (pending a root-capable host)
before claiming an endpoint win. Kernel-level atomic commit and hard-link
identity remain out of scope, as documented in Steps 10 and 23.
