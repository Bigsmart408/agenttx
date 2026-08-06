# Step 12 - Content-addressed rollback snapshots

## Problem

Copying the complete upperdir before every step gives each rollback snapshot
independent files, but repeats unchanged file contents. Long trajectories can
therefore grow storage approximately with the product of steps and accumulated
upperdir size. The remote VM filesystem does not support reflinks, so a kernel
CoW clone is not portable here.

## Design

`LayerStore.snapshot_before` now stores regular-file contents in immutable blobs
under `layers/blobs/`. A snapshot tree owns its directories, symlinks,
whiteouts, and metadata, while regular-file entries hard-link to their blob.
Blob keys include the file identity, metadata, and content fingerprint. When the
caller already has the previous `upperdir_digests` (the normal AgentTX path),
the fingerprint is reused without rereading the file. Direct LayerStore callers
fall back to a content hash, preserving correctness even when timestamp
resolution is coarse.

The live upperdir never shares a regular-file inode with a snapshot blob: a
subsequent in-place write cannot mutate a pre-step image. Dropping snapshots
performs link-count garbage collection for unreferenced blobs. Whiteouts remain
hard-linked as before, and restrictive modes are temporarily relaxed only while
copying.

## Evidence

`experiments/scripts/bench_snapshot_storage.py` creates 128 64KiB files and 12
snapshots while changing one file per step. The logical snapshot payload is
100,663,296 bytes; unique physical file bytes are 9,109,504 bytes (ratio
0.090). The run takes 0.425 seconds on the VM.

## Remaining boundary

Directory and metadata traversal still occurs for every snapshot. Commit WAL and
historical commit reconstruction also copy a live upperdir temporarily. A
future delta-manifest design can reduce traversal and WAL copy cost, while
hard links, ownership, extended attributes, and arbitrary special files remain
outside the complete model.
