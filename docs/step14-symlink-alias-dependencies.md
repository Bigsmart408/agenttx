# Step 14 - Symlink alias causal dependencies

## Problem

A trace collected with `strace -yy` can report the resolved inode path for a
read opened through a symlink. If the ledger records only that resolved path,
it loses the dependency on the step that created the symlink. Conversely, a
trace without fd resolution may report only the alias and miss a producer that
wrote the target path.

## Design

`parse_strace_effects` now retains both paths when an fd return identifies a
different resolved path from the requested open path. `AgentTX` additionally
resolves symlink ancestors in the merged host/upperdir view and adds canonical
effects without replacing the original path. This gives the ledger both edges:

- alias path ? the symlink producer; and
- canonical target path ? the file producer.

Resolution is bounded to 40 symlink hops and only adds canonical paths that
remain inside the workspace. It does not dereference the final component, so a
`readlink` or symlink creation effect remains attached to the link itself.

## Verification

Trace unit tests verify that an `openat` through `alias/data.txt` records both
the requested alias and the resolved `real/data.txt`. Real-try tests cover a
lower-layer symlink and an upperdir-created symlink; causal rollback includes
the correct producers and consumers while preserving unrelated host state.

## Remaining boundary

Symlink loops and escapes fail closed by retaining the original path. Hard links,
bind mounts, mount namespaces, case-folding filesystems, and non-filesystem
effects still require separate identity or mediation models.
