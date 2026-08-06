# Step 3 — Surgical cascade rollback

`try -L` stacking needs mergerfs on this VM and failed in probing.
Instead, AgentTX snapshots the shared `upperdir` before each step under
`session/layers/before_NNNN/`.

Cascade rollback of steps `[i..]` restores `upperdir` to `before_i` and
drops later snapshots — without destroying the whole session metadata.

## Demo

```bash
PYTHONPATH=src python3 experiments/scripts/demo_surgical_rollback.py
```
