# Step 8 - Whiteout-safe rollback snapshots and metadata commit

## Failure found

A lower-layer deletion is represented by OverlayFS as a character device with
major/minor 0/0. The previous shutil.copytree snapshot code tried to open that
whiteout like a regular file. Therefore, deleting a file in step 0 and running
any step 1 failed before execution with Permission denied.

A related probe found that mode-000 directories hid descendants from effect
fingerprinting and could not be copied or removed during rollback. Upstream try
also creates the directory at its restrictive final mode before moving child
files, reports that move failure in stderr, but can still exit with status 0.

## Snapshot and restore design

LayerStore now uses an upperdir-specific tree copier:

- regular files are independent copy2 copies;
- symlinks are recreated without dereferencing;
- native 0/0 character-device whiteouts are hard-linked into the snapshot;
- owner read/search permissions are added temporarily for mode-000 trees and
  restored before the operation returns; and
- unsupported special files fail closed instead of being silently skipped.

The snapshot root and upperdir are both inside the same session directory, so
whiteout hard links remain on one filesystem and do not require privileged
mknod. Restore uses the same copier. Tree removal first grants owner access so
restrictive directories cannot leave a partially restored upperdir behind.

Effect fingerprinting uses the same temporary-access principle. It can now see
and hash regular files below mode-000 directories while retaining their
original mode in the digest.

## Commit correctness

Before invoking try commit, AgentTX captures mode and timestamps for selected
upperdir entries. It temporarily grants owner read/write/search access so try
can materialize children, restores the speculative upperdir modes afterward,
and applies final metadata child-first on the host. Child-first ordering avoids
making a parent non-searchable before its descendants are finalized.

The vendored try implementation may print "couldn't commit" while returning
status 0. AgentTX recognizes that error marker and returns a failed commit, so
the ledger frontier does not advance on a known partial materialization.

## Verification

Real-try integration tests now cover:

1. delete a lower file, execute a later step, roll back the later step, and
   commit the preserved whiteout; and
2. create a file under a mode-000 directory, execute and roll back a later
   step, then commit both content and the final directory mode.

The full suite passes (37 tests), and all eight evidence-suite scenarios still
pass with frontier-selective commit enabled.

## Remaining boundary

Hard-link identity, ownership transitions, extended attributes, and arbitrary
special files remain outside the complete effect model. Most importantly, a
failed or interrupted upstream commit may still be visible to an external
observer while it is in flight. Step 10 adds a durable WAL that
restores partial host materialization on session reload; kernel-level atomicity
across independent paths remains out of scope.
