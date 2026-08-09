# Step 23: Lower hard-link / OverlayFS semantic boundary

## Why this probe exists

Switching the default API from temporal rollback to causal rollback requires
confidence that path aliases do not hide dependencies. Symlink ancestors are
already canonicalized, but the review identified hard links and bind mounts as
unresolved. Hard links cannot be handled safely by adding inode-equivalence
edges until the underlying speculative filesystem preserves their data
visibility semantics.

## Probe

`experiments/scripts/probe_hardlink_alias.py` creates two lower-workspace names
for one inode, starts AgentTX, writes `first.txt`, reads `alias.txt` in the same
session, and selectively commits the writer. A normal POSIX write through one
hard link is visible through the other name and preserves one inode.

On the current Linux 5.15 VM and `try` OverlayFS path, the experiment observes:

- before AgentTX, both names share one inode with link count 2;
- after writing `first.txt`, reading `alias.txt` inside the speculative overlay
  returns the old value;
- the reader has no dependency on the writer because it did not observe the
  writer's data;
- after selective commit, `first.txt` contains the new value, `alias.txt`
  contains the old value, and the paths no longer share an inode.

## Consequence

The issue is deeper than ledger canonicalization: lower hard links are split by
OverlayFS copy-up before AgentTX sees post-step effects. Treating the paths as
equivalent in the ledger would claim a data dependency that the speculative
process did not actually observe, while still failing to repair in-tool reads
or preserve link topology at commit.

Faithful support needs a mechanism that acts at or below each write (for
example, a FUSE layer that owns inode identity, kernel-assisted interception,
or a different snapshot substrate). A post-tool userspace mirror cannot repair
a read through the alias that already occurred inside the same opaque command.

Therefore causal rollback remains explicit rather than becoming the default.
This subtask is paused as an environment/substrate issue, consistent with the
project rule to defer kernel-dependent work. Bind-mount aliasing remains
separately untested because the VM does not expose the required mount authority.

## Reproduce

```bash
cd /home/bfq/agenttx
PYTHONPATH=src:. /home/bfq/miniconda3/envs/agenttx/bin/python \
  experiments/scripts/probe_hardlink_alias.py
```

Artifacts are `experiments/results/hardlink_alias_probe.{json,md}`.
