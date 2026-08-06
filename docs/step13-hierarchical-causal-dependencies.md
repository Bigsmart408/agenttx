# Step 13 - Hierarchical causal path dependencies

## Problem

The initial ledger matched effects only by exact path string. A producer that
created `/workspace/pkg` could therefore fail to become a parent of a later
read of `/workspace/pkg/data.txt`; a negative lookup of `/workspace/missing`
could likewise miss a nested write. Causal rollback would then retain a
descendant step whose filesystem state depended on the rolled-back directory.

## Design

`Ledger.add_step` now compares path effects with lexical parent/child overlap:

- READ and NEGATIVE effects depend on prior WRITE/DELETE effects at the same
  path or an ancestor/descendant path;
- WRITE and DELETE effects depend on overlapping prior writes/deletes; and
- WRITE effects depend on overlapping prior negative lookups.

Committed steps remain outside the speculative writer index because their state
is already durable. The graph stores only step ids, so the serialized ledger
format remains backward-compatible.

This is deliberately lexical rather than full pathname resolution. Symlinks,
bind mounts, hard links, case-folding filesystems, and other aliases require a
separate identity model.

## Verification

Unit tests cover parent directory writes, child reads, and nested writes after a
negative lookup. A real-try integration test creates `pkg/data.txt`, reads it
in a later step, and verifies causal rollback removes both producer and
consumer while retaining an independent file.
