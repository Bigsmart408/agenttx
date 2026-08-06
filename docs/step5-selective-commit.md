# Step 5 - Ledger-driven selective filesystem commit

## What landed

`AgentTX.commit(up_to)` no longer commits the whole shared upperdir. The runtime:

1. collects write/delete paths from active ledger steps after the previous frontier and through `up_to`;
2. rejects the partial commit if a later speculative write overlaps any selected path;
3. passes anchored include filters to binpash/try (`try -I ... commit`); and
4. advances the ledger frontier only after the filtered filesystem commit succeeds.

Parent directories are included with exact suffix patterns so new nested paths can be materialized without selecting sibling effects. Independent later paths remain in the shared overlay.

## Delete correctness

OverlayFS represents deletion of a lower-layer file as a character-device whiteout on the development VM. `upperdir_digests()` now recognizes both character-device and `.wh.<name>` whiteouts and emits `EffectKind.DELETE`. Symlink targets are also fingerprinted rather than silently skipped.

## Fail-closed boundary

If step 0 writes `a.txt` and a later speculative step rewrites `a.txt`, the current upperdir holds only the later version. Committing through step 0 therefore raises `ValueError`; it never materializes the wrong version. Historical same-path reconstruction from step snapshots remains future work.

## Verification

```bash
cd /home/bfq/agenttx
export PATH="$HOME/miniconda3/envs/agenttx/bin:$PATH"
export PYTHONPATH=src:.
python -m pytest -q
```

The real-try integration suite verifies independent-path partial commit, direct lower-file deletion, overlap rejection, and rollback followed by a new commit. Test failures are no longer converted into successful exits.
