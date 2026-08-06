# Step 9 - Explicit non-contiguous causal rollback

## Problem

The temporal rollback API removes a failed step and every later speculative
step. That is safe but over-aborts independent work. Once automatic READ and
NEGATIVE effects are available, the ledger can identify the smaller transitive
causal closure.

## API and reconstruction

AgentTX.rollback_causal(step_id) computes Ledger.causal_dependents(step_id).
For the selected closure it collects WRITE/DELETE paths and restores only those
logical paths from the upperdir snapshot taken before the earliest target.
Independent later upperdir entries remain intact. The layer store handles
regular files, symlinks, native whiteouts, empty directories, and restrictive
modes using the same copier as ordinary rollback.

After reconstruction, only target steps are marked rolled_back. The retained
steps remain speculative and can be committed through the normal frontier API.
The CLI exposes the behavior as rollback --causal. Existing rollback() keeps
temporal semantics for compatibility while this API is evaluated.

## Fail-closed conditions

The runtime rejects causal reconstruction if any retained effect overlaps a
target WRITE/DELETE path, including parent/descendant paths. This covers a
retained write into a directory whose creation or replacement is being
removed. It also refuses to cross the committed frontier. If restoration fails,
the ledger is not marked rolled back.

This is deliberately narrower than claiming general filesystem replay:
unmodeled hard links, bind mounts, aliases, and non-filesystem effects remain
outside the reconstruction proof.

## Verification

Real-try tests cover:

- a producer write, an independent later write, and a consumer read; causal
  rollback removes producer and consumer while preserving and committing the
  independent file;
- lower-layer deletion rollback via an OverlayFS whiteout; and
- fail-closed rejection when a retained descendant path overlaps a target
  directory.

The full suite passes (39 tests), including the existing eight-scenario
evidence suite.

## Remaining work

Causal rollback is explicit rather than the default until path aliasing and
hierarchical dependency coverage are stronger. A crashed reconstruction or
commit still needs WAL-based recovery, and replay traversal remains tied to upperdir snapshot size, although Step 12
deduplicates repeated regular-file content.
