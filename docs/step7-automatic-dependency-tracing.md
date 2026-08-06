# Step 7 - Automatic read and negative-lookup dependencies

## Why upstream try -t was not used

The vendored try tracer fixes its raw strace path at /run/try_trace.log. On the
development VM, /run cannot be mounted as an overlay, so both regular and
shared-session try -t fail before producing a parsed log. Its parser also
collapses failed lookups into ordinary reads, losing the distinction required
for negative dependencies.

## Design

For each traced tool call, AgentTX runs strace inside the same try namespace and
writes a uniquely named raw log to overlay-backed /tmp. After the command:

1. AgentTX parses successful path reads plus ENOENT/ENOTDIR failures;
2. it retains only paths strictly below the transaction workspace;
3. it removes the internal raw log before upperdir fingerprinting; and
4. it combines read/negative effects with overlay-derived writes/deletes.

The raw log name includes a session hash, process id, and step id. Crash
leftovers are explicitly excluded from effect fingerprinting and cannot enter a
ledger-selected commit.

Tracing is enabled by default. Session metadata persists the trace_reads mode.
Initialization fails closed if strace is unavailable; callers and the CLI may
explicitly opt out with trace_reads=False or --no-trace-reads.

## Dependency semantics

A READ or NEGATIVE effect depends on the latest uncommitted write/delete to the
same path. A later write also depends on an earlier NEGATIVE effect for that
path. This captures both producer-consumer flow and absence-sensitive control
flow:

- step 0 writes input.txt;
- step 1 reads input.txt and observes missing.txt absent; and
- step 2 creates missing.txt.

The resulting edges are 0 -> 1 -> 2 without manual extra_reads hints.

## Verification and cost

Unit tests exercise read/write discrimination, negative lookups, chdir plus
child-process cwd inheritance, missing-strace fail-closed behavior, CLI opt-out,
and trace-mode session reload. A real try/strace integration test verifies the
three-step causal chain above.

The reproducible no-op benchmark uses 10 steps and three repeats:

| mode | per_step_ms_mean | per_step_ms_stdev |
|---|---:|---:|
| trace_off | 184.35 | 7.57 |
| trace_on | 202.26 | 7.29 |

The measured increment is 17.91 ms/step (9.7%). Overlay setup remains the
dominant per-step cost.

## Remaining boundary

The parser intentionally covers common open/stat/access/readlink/exec/chdir
families and workspace-local paths. It is not yet a complete Linux VFS model:
exact-path matching misses some hierarchical aliases, inherited pre-opened file
descriptors are not reconstructed, and extended attributes, hard-link identity,
and non-filesystem effects remain incomplete. These limitations must be
measured before making a complete causal-isolation claim.
