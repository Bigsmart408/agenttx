# Step 6 - Durable recovery and complete basic filesystem effects

## What landed

AgentTX now preserves the transaction's snapshot sequence across
AgentTX.load(session_dir). The resumed shared semisolate starts its next layer
at the ledger's next step id, so a post-restart tool call cannot overwrite an
earlier before_NNNN rollback snapshot.

Session metadata is written to a temporary file in the session directory,
flushed with fsync, atomically installed with os.replace, and followed by a
parent-directory fsync. If replacement fails, the prior agenttx.json remains
readable and the temporary file is removed.

## Filesystem effect coverage

Upperdir fingerprints now encode entry type and relevant metadata:

- regular files: mode, owner, group, nanosecond mtime, and content;
- symlinks: mode, owner, group, nanosecond mtime, and target;
- directories below the workspace: mode, owner, and group; and
- OverlayFS character-device and .wh.* whiteouts: delete markers.

This records empty-directory creation, repeated metadata-only updates, and both
sides of a rename. Directory mtimes are intentionally excluded because child
effects already identify structural changes; including them would produce a
parent rewrite for every file creation.

## Verified invariants

The real-try integration tests cover:

1. resume, execute another step, rollback only that step, then commit earlier speculation;
2. injected os.replace failure without corrupting the old session metadata;
3. empty-directory materialization;
4. two same-content chmod changes both appearing in the ledger; and
5. lower-layer rename as source delete plus destination write.

The full unit/integration suite passes (21 tests), and the eight-scenario
evidence suite still passes with frontier-selective commit enabled.

## Remaining boundary

This step makes metadata durable, not the multi-path host commit itself. A crash
inside try commit can still leave a partially materialized host workspace while
the ledger frontier remains old. A write-ahead commit/recovery protocol is
required before claiming crash-atomic transactions. Extended attributes,
hard-link identity, FIFOs/devices, automatic reads, and negative lookups also
remain outside the current effect model.
