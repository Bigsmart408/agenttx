# Step 10 - Crash-recovery WAL for multi-path commit

## Failure window

`try commit` materializes several selected paths through separate host
operations. A process loss after one path lands but before `agenttx.json`
advances the frontier could previously leave the host and ledger disagreeing.
The metadata file itself is atomic, but metadata atomicity does not undo an
external filesystem operation that already happened.

## WAL protocol

Before invoking `try commit`, `AgentTX.commit_frontier` creates
`commit_wal.json` in the session directory and a durable `.commit-wal-backup`
containing:

- the pre-commit image of selected workspace paths; and
- the complete speculative upperdir, including native whiteouts and restrictive
  modes.

The WAL advances through `prepared`, `applying`, `materialized`, and
`committed` phases. The ledger frontier is persisted only after `try commit`
returns success and the WAL reaches `materialized`. Cleanup removes the intent
first, so an interrupted backup deletion cannot leave a WAL without its
pre-image.

On `AgentTX.load`, recovery is fail-closed:

- a prepared/applying WAL restores the host and upperdir pre-image;
- a materialized WAL with an old frontier restores the pre-image; and
- a materialized/committed WAL with the new frontier is finalized and cleaned.

This makes a process crash retryable without silently accepting a partially
committed frontier. It does not claim that an external observer cannot see a
partial host state during the in-flight `try commit`; that requires a kernel or
filesystem transaction primitive.

## Verification

`tests/test_recovery.py` injects a process-loss-like `KeyboardInterrupt` after a
partial host write. Reload restores the old host contents, preserves the
speculative overlay, removes the WAL, and successfully retries the commit. The
existing metadata-replace and session-resume tests continue to pass.

## Remaining boundary

The pre-image cost scales with the selected host tree and upperdir. Hard links,
ownership transitions, extended attributes, arbitrary special files, and
non-filesystem effects remain outside the complete recovery model.
