# Step 11 - Historical same-path frontier commit

## Problem

A partial frontier commit previously failed closed whenever a later
speculative step rewrote a selected path. That protected correctness but forced
users to roll back or commit all versions, even when the earlier version was the
intended frontier.

## Reconstruction

`AgentTX.commit_frontier(up_to)` now computes selected and later write sets.
When their paths overlap, it chooses the snapshot taken before the first
retained later step. `SharedSemisolate.commit_from_snapshot` then:

1. copies the current upperdir aside;
2. replaces the upperdir with that historical snapshot;
3. runs anchored path-selective `try commit`; and
4. restores the current upperdir, leaving later speculative writes intact.

The commit WAL already protects the host pre-image and current upperdir, so a
process loss during the temporary reconstruction restores both images on reload.
The ledger advances only through `up_to`; later steps remain speculative and can
commit their newer versions later.

## Safety boundary

A missing historical snapshot fails closed. Path aliases, hard links, bind
mounts, ownership and extended attributes remain outside the filesystem model.
The temporary upperdir swap is serialized with the session commit path and never
exposes speculative upperdir content to the host.

## Verification

The real-try integration test writes `same.txt` twice and an independent later
file. `commit(0)` materializes `old`, leaves both later steps speculative, and a
subsequent full commit materializes `new` plus the independent file. The full
suite and evidence suite continue to pass.
