# Step 28: Persistent eBPF dependency tracing

## Motivation

The original `bpf` backend starts a new `bpftrace` process for every tool
call.  That is a useful correctness baseline, but on the 244 host its
per-step cost is dominated by repeated probe teardown (`SIGINT`) and attach
setup.  A long Agent workload therefore pays the same tracing startup tax
hundreds of times.

## Design

`trace_backend="bpf"` keeps one bpftrace process attached for the
life of a `SharedSemisolate` session and reuses the existing persistent `try`
worker.  A release marker still holds each command until the first tracer is
ready.  The reader thread drains the tracer's stdout continuously; at each
step the runtime slices the new lines, snapshots the worker's process tree,
and sends the slice through the existing `parse_bpf_effects` logic.  Thus the
correctness boundary is unchanged: eBPF only supplies syscall observations,
while the ledger still derives causal edges from the resulting effects.

The persistent mode is the only eBPF mode.  The former per-step attach path
and its high-latency benchmark mode were removed; `bpf` is now the explicit
session-persistent backend, and `auto` keeps its previous selection behavior.
If the worker dies, the persistent tracer is stopped before sandbox repair;
the worker and tracer are then restarted together. There is no per-step attach
fallback, so an unrecoverable restart fails the step closed.
Commit, rollback, reset, summary, and close all stop the tracer before they
tear down the worker namespace.

## Measurement

Command (run as root on x86/244):

```bash
PYTHONPATH=src python3 experiments/scripts/bench_bpf_trace.py \
  --steps 12 --repeats 2 --workload read
```

The workload executes `cat input.txt >/dev/null` and a negative lookup for
`missing.txt` on every step.  The benchmark verifies both effects for every
traced step and reports mean/p50/p95 per-step latency.

| mode | mean (ms/step) | p50 | p95 | READ/NEGATIVE capture |
|---|---:|---:|---:|---:|
| no tracing | 17.70 | 2.99 | 178.93 | — |
| strace | 25.60 | 11.67 | 183.29 | 24/24, 24/24 |
| persistent eBPF (`bpf`) | 61.39 | 26.29 | 413.54 | 24/24, 24/24 |

The removed per-step implementation is not part of the maintained backend or
benchmark. The persistent process removes that repeated attach/teardown path;
on this indexed OverlayFS host it still has a higher p50 than strace because
each step drains the long-lived trace stream and filters the process tree. It
does not change path-based tracing coverage or solve fd-based identity by
itself. The existing caveats
about hard-link/bind aliases and fd-based reads therefore remain unchanged.

## Reproduction and interface

```python
tx = AgentTX.begin(
    workdir=workspace,
    trace_reads=True,
    trace_backend="bpf",
)
```

The CLI accepts `agenttx begin --trace-backend bpf`; this is the only eBPF
mode and keeps one attachment across the session. The removed per-step mode is
not part of the maintained comparison.
