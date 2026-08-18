# Step 29 design: hard-link-preserving OverlayFS transactions

Status: **partially landed (P0/P1)**  
Target: AgentTX on Linux with the `try` OverlayFS backend  
Scope: regular-file hard links inside configured transaction roots

## 1. Decision

AgentTX should not try to repair this problem in the tracer or by adding a
path-only dependency edge. The fix needs three coordinated mechanisms:

1. mount the active speculative view with OverlayFS inode indexing enabled;
2. represent hard-linked names as one versioned object in the ledger and in
   rollback snapshots;
3. materialize and recover the object graph with a hard-link-aware commit
   path instead of moving each upper file independently.

The recommended implementation is an **indexed live view plus logical topology
snapshots**. `index=on` gives commands the correct same-mount POSIX visibility.
At every boundary where AgentTX unmounts, rolls back, commits, or recovers, it
normalizes the state into an AgentTX-owned object/alias representation. The
OverlayFS workdir index is treated as volatile kernel metadata, not as a
durable snapshot format.

If the kernel or backing filesystem cannot pass the required `index=on`
semantic probe, AgentTX may use the bounded promotion fallback described in
Section 10. Otherwise it must fail closed for a transaction that can write a
hard-linked object. Silent fallback to `index=off` is not acceptable.

## 2. Root cause and new evidence

The current bootstrap patch constructs every OverlayFS mount with
`index=off`. This is not merely an inode-number presentation issue. The Linux
OverlayFS documentation states that when the index feature is disabled,
copying up a multiply-linked file breaks the link and changes are not
propagated to its other names:

<https://docs.kernel.org/filesystems/overlayfs.html#index>

That description exactly matches the existing Step 23 result.

An isolated mount-namespace probe was run on the active 244 host on
2026-08-14:

```text
host: pengpeng-ubuntu-01-1
kernel: 5.4.0-216-generic
architecture: x86_64
```

The same lower directory, containing `first` and `alias` as two names for one
inode, was tested with both index modes:

| mode | write through `first` | read through `alias` | merged identity |
|---|---|---|---|
| `index=off` | `new` | `old` | different inode; link broken |
| `index=on` | `new` | `new` | same inode; `nlink=2` |

The exact observed object tuples were:

```text
index=off
  first  92:5711690:1
  alias  92:5163023:2

index=on
  first  92:5711696:2
  alias  92:5711696:2
```

The tuple format is `st_dev:st_ino:st_nlink`. This proves that the target
kernel already has a working mechanism for the in-command visibility failure;
a new FUSE filesystem is not the first required step.

## 3. Why `index=on` alone is insufficient

Changing one mount option fixes the active merged view, but the rest of the
current AgentTX pipeline is path-oriented:

- the vendored `try` commit path uses `mv` for a modified or added file;
  replacing one host directory entry detaches it from its hard-link siblings;
- `LayerStore._copy_overlay_tree()` copies regular entries independently, so a
  restore can turn one source inode with two names into two destination inodes;
- `CommitWAL._copy_host_entry()` has the same independent-copy behavior, so a
  crash restore cannot promise the original link graph;
- `upperdir_digests()` fingerprints content and metadata but not object
  identity or link topology;
- ledger effects name paths only, so a write to `first` and a read from
  `alias` cannot meet at one object key;
- the OverlayFS workdir index is not included in AgentTX snapshots.

There is an additional lifecycle constraint. Kernel documentation says that
changing an underlying lower tree offline is undefined when the index feature
has been used. AgentTX partial commit currently unmounts, modifies the host
lower tree, retains a speculative upper suffix, and mounts again. Therefore an
indexed upper must be normalized/rebased before a host commit; AgentTX must
never retain stale `trusted.overlay.origin`/workdir index state across a lower
generation change.

Consequently, the complete fix is an object-topology change, not a mount-flag
change.

## 4. Correctness invariants

The implementation is complete only when all of the following hold.

**H1 — merged visibility.** If two names refer to one object at step start, an
ordinary in-place write through either name is immediately visible through the
other name in the same opaque command and in later commands.

**H2 — intentional split.** Replacing one name, for example with
`rename(temp, first)`, may intentionally detach that name. AgentTX must retain
the new two-object topology instead of forcing the names back together.

**H3 — causal identity.** A read and a write meet on a session object id, not
only on equal path strings. A write through `first` must become a parent of a
later read through `alias` while the two names remain linked.

**H4 — rollback identity.** Temporal and causal rollback restore content,
metadata, alias membership, and link counts for the selected object version.

**H5 — commit identity.** Commit preserves the desired host link graph. It
must not replace one name with an unrelated inode when the committed state
says that the names remain linked.

**H6 — crash identity.** WAL recovery restores the pre-commit link graph as
well as bytes and metadata.

**H7 — scope safety.** A group with aliases outside authorized transaction
roots is not committable by default. AgentTX must report the unresolved alias
count and require a wider root or an explicit policy decision.

## 5. Object and alias model

Add a session-level `HardlinkCatalog`. A regular file with `st_nlink > 1` is
represented by a stable AgentTX object id and a set of names valid in a given
version:

```text
ObjectState
  object_id          random session UUID, never a bare inode number
  base_token         (mount_id, st_dev, st_ino, ctime_ns)
  aliases            sorted paths inside authorized roots
  observed_nlink     statx st_nlink
  complete           observed_nlink == number of discovered aliases
  content_blob       immutable snapshot blob id, when materialized
  metadata           mode, uid, gid, times, xattrs/ACL digest
  generation         overlay/lower generation in which the state is valid
```

Inode numbers are evidence for equality inside one mount generation; they are
not durable ids. `object_id` survives copy-up, snapshot restore, and host
materialization. `base_token` is used for optimistic conflict detection.

At session start AgentTX scans configured transaction roots with `lstat` or
`statx`, groups regular files by `(mount_id, st_dev, st_ino)`, and records
groups with `st_nlink > 1`. `st_nlink` larger than the discovered alias count
marks a partial/external group.

After each command, AgentTX collects `statx` for:

- paths written, linked, unlinked, or renamed by the command;
- known aliases of the affected objects;
- new entries in touched parent directories.

The post-step equality classes determine whether the tool preserved, split,
merged, created, or removed links. This is important because identical bytes
do not imply identical objects.

## 6. Active OverlayFS view

### 6.1 Mount configuration

Change the `try` bootstrap integration so index mode is explicit instead of
hard-coded off:

```text
TRY_OVERLAY_INDEX=on|off|auto
```

AgentTX production mode uses `on`. `off` exists only for the Step 23 regression
baseline. `auto` means "run the semantic probe, use on only after it passes";
it must not silently accept a failed hard-link probe for a writable group.

`xino=auto` may be enabled to improve `stat` identity stability, but `xino`
does not preserve hard-link data visibility and is not a substitute for the
index feature.

### 6.2 Capability probe

The existing backend probe should be extended to test semantics, not merely
whether `mount(2)` succeeds:

1. create two lower names for one inode;
2. mount the same upper/work filesystem configuration used by the workspace;
3. write through name A and read through name B;
4. require the new bytes, equal `(st_dev, st_ino)`, and `st_nlink == 2`;
5. verify the effective mount options contain `index=on`;
6. unmount and remove the scratch tree.

The result and reason are persisted in `agenttx.json` so reload cannot change
substrate semantics silently.

### 6.3 Persistent-worker rule

The indexed merged mount should stay alive across ordinary tool steps. This is
already aligned with the persistent `try` worker. AgentTX may read the upper
tree to snapshot it, but must not modify upper or lower while the indexed
overlay is mounted.

Worker teardown, rollback, commit, and recovery are generation boundaries.
They must first unmount, then normalize logical object state, then discard the
volatile workdir index.

## 7. Topology-aware snapshots and rollback

Raw upperdir layout is not the durable representation. An indexed copy-up may
have one visible upper name plus a workdir index hard link even though several
merged names address the object. AgentTX snapshots must instead save a logical
object graph:

```text
before_NNNN/
  tree/             pure-upper files, dirs, symlinks, whiteouts
  topology.json     object id -> aliases, metadata, blob id, generation
```

For each object, content is copied to the content-addressed blob store once.
All materialized aliases in `tree/` are created with `linkat` from one inode.
Overlay-private origin/index xattrs are not copied into the logical snapshot.
The snapshot is therefore independent of the kernel workdir index.

Required changes to `layers.py`:

- make all tree-copy helpers carry an inode memo
  `(source_dev, source_ino) -> first_destination`;
- use `os.link` for later regular-file aliases instead of `shutil.copy2`;
- store alias membership and link count in the snapshot manifest;
- include object id/topology in incremental snapshot fingerprints;
- expand one changed group member to the whole group before creating an
  incremental snapshot;
- restore a group as linked pure-upper entries, not independent copies.

Rollback operates on **object-group closure**. If a selected step modified an
object while its names remained linked, all those names are restored from the
same object snapshot. The retained-effect overlap check also expands paths to
object groups, preventing AgentTX from retaining a later alias write that
would be silently overwritten by the rollback.

When a rollback intentionally restores a split, the manifest contains two
object ids and restoration creates two inodes. AgentTX must never infer links
from equal content hashes.

## 8. Ledger changes

Extend `Effect` with optional object information while keeping `path` for
policy and diagnostics:

```text
Effect
  path
  kind                 R / N / W / D
  object_id            object read or written, when known
  object_version       version after the step
  topology_op          link / unlink / replace / rename / none
```

Dependency construction uses `object_id` for regular-file RAW dependencies and
continues to use canonical paths for negative lookups and directory hierarchy.
The path displayed in the ledger remains the name actually used by the tool.

For a content/metadata write that preserves a hard-link group, the step may
store one object write plus the alias set rather than duplicating large effect
lists. `_commit_path_plan()` and rollback planning expand the group only at the
policy/materialization boundary.

Trace backends help identify requested names and topology syscalls, but they
are not the source of truth for the final link graph. The merged-view `statx`
snapshot at the tool boundary is authoritative. The existing eBPF/kernel
identity work remains useful observation input; it cannot replace this
substrate and materialization design.

## 9. Hard-link-aware commit and WAL

Hard-linked groups bypass the upstream path-wise `try commit`. Unlinked regular
files may continue using the existing path when they do not participate in an
object operation.

### 9.1 Commit planning

The plan is built from the requested ledger frontier and its historical
logical snapshot:

1. expand every selected path to the committed object-group closure;
2. run commit policy against every affected alias;
3. reject incomplete/external groups by default;
4. compare current host base tokens with the begin-time tokens and fail on a
   concurrent topology/content conflict;
5. derive desired object groups and name operations;
6. prepare WAL v2 with the complete affected alias graph.

Selective commit of a write to one name of a still-linked object is therefore
an object commit, not a one-directory-entry commit. This is the only meaning
consistent with the command's POSIX behavior.

### 9.2 Materialization cases

| desired change | materialization rule |
|---|---|
| mutate existing linked object | write bytes/metadata through one existing inode; do not rename over one alias |
| create another name | create a temporary hard link to the desired object, then rename it into the new path |
| unlink one name | remove only that directory entry; other links remain |
| replace/split one name | stage a new inode and rename it over that name |
| merge names into one object | choose/stage one inode and install all desired names as links to it |
| delete final name | unlink the final directory entry after WAL is durable |

There is no Linux primitive that atomically swaps the bytes of an inode while
preserving all hard links. For an in-place content update, AgentTX relies on an
exclusive workspace commit lock, ordered fsync, and WAL recovery. This matches
the project's current crash-recovery model, which is durable but not externally
atomic across several host paths. That limitation should remain explicit.

### 9.3 WAL v2

The current WAL independently copies each selected host path and therefore
cannot restore link identity. WAL v2 records:

- all affected names, including expanded aliases;
- pre-image equality classes and their metadata;
- one content backup per pre-image object;
- absent/present state for every name;
- the pre-commit upper logical snapshot and generation;
- ordered planned operations and the last durable operation number.

Backup and restore use an inode memo and `linkat`, just like LayerStore.
Recovery verifies the restored alias graph with `statx` before deleting the
intent record.

### 9.4 Partial frontier rebase

After committing into the host lower tree, AgentTX must not remount a retained
indexed upper against that modified lower generation. Before host mutation it
captures the speculative suffix as a logical topology snapshot. After commit
it creates a new generation:

1. discard the old OverlayFS workdir index;
2. restore the retained suffix as pure-upper linked objects without origin
   xattrs;
3. set the newly committed host tree as the next lower generation;
4. mount a fresh indexed view;
5. verify the object catalog against the merged view.

This rebase also provides a clean recovery point after worker failure.

## 10. Bounded fallback: eager hard-link promotion

Some backing filesystems cannot support OverlayFS indexing. For a transaction
whose authorized roots contain only complete, enumerable hard-link groups,
AgentTX can preserve semantics without the kernel index by promoting those
groups before the first tool call:

1. scan and group all regular files with `st_nlink > 1`;
2. copy one file per group into upper while preserving metadata/xattrs;
3. create all sibling upper names with `linkat`;
4. record these entries as the speculative baseline, not as writes;
5. continue with `index=off` and the same object-aware snapshot/commit path.

Because every group is already a pure-upper hard-link set, a write through one
name is visible through the others even inside one opaque command.

Promotion is rejected when:

- `st_nlink` is greater than the number of aliases found in authorized roots;
- a member is on another mount or cannot be safely opened with no symlink
  traversal;
- required ownership, ACL, capability, or xattr metadata cannot be preserved;
- configured size/count limits would be exceeded.

This is a compatibility fallback, not the default fast path. A FUSE or custom
kernel snapshot substrate is only justified if native indexing and bounded
promotion both fail the deployment requirements.

## 11. Implementation map

Suggested code changes:

| area | change |
|---|---|
| `scripts/bootstrap.sh` / vendored `try` patch | configurable index mode and semantic capability probe |
| new `src/agenttx/object_identity.py` | `HardlinkCatalog`, statx tokens, alias discovery, group closure |
| `src/agenttx/effects.py` | optional object id/version/topology operation |
| `src/agenttx/ledger.py` | object-keyed RAW dependencies and group expansion |
| `src/agenttx/semisolate.py` | post-tool topology capture, generation normalization, pure-upper restore |
| `src/agenttx/layers.py` | topology manifest and inode-memo copy/restore |
| `src/agenttx/runtime.py` | object-level rollback and commit planning, rebase |
| `src/agenttx/commit_wal.py` | WAL v2 alias graph and link-preserving recovery |
| `experiments/scripts/probe_hardlink_alias.py` | `index=off/on`, rollback, commit, crash matrix |

The feature should be guarded initially by:

```text
hardlink_mode = reject | indexed | promote
```

Default progression is `reject`, then `indexed` after the full acceptance
suite passes. `promote` remains an explicit compatibility mode.

## 12. Rollout plan

### Phase A — substrate and fail-closed gate

- add the exact semantic mount probe;
- expose effective index mode in `status()`;
- detect hard-link groups and reject writes/commit when running unsupported;
- retain `index=off` as a regression baseline.

### Phase B — correct speculative view and causal edges

- enable `index=on` for the active persistent mount;
- add `HardlinkCatalog` and topology-aware effects;
- require `writer(first) -> reader(alias)` in the ledger;
- handle link, unlink, rename-over, chmod, and open-fd writes.

### Phase C — logical snapshots and rollback

- add snapshot topology manifests and inode-memo copies;
- implement object-group closure for temporal and causal rollback;
- normalize indexed state at worker teardown;
- validate reload and worker-crash recovery.

### Phase D — commit and WAL v2

- implement native object materialization;
- expand policy/WAL scope to all aliases;
- add lower-generation rebase after partial commit;
- turn indexed mode into the default only after crash-injection tests pass.

## 13. Acceptance matrix

The following cases must be tested both in one opaque shell command and across
separate AgentTX steps:

| case | required result |
|---|---|
| lower `a`/`b`, write `a`, read `b` | new bytes; same object; causal parent present |
| write through an already-open fd | both names observe new bytes |
| chmod/chown/touch through one name | metadata visible through all names |
| `link(a, c)` | three names, one object after rollback/commit/reload |
| `unlink(a)` | `b` remains valid with decremented link count |
| rename a new file over `a` | intentional split is retained |
| temporal rollback | original bytes and link graph restored |
| causal rollback with independent later file | linked object restored; independent file retained |
| selective commit of linked write | host aliases remain linked and all expose committed bytes |
| partial commit with speculative suffix | fresh lower generation plus correct retained view |
| crash before/during/after each materialization op | WAL recovery restores one valid pre- or post-state graph |
| external/partial group | commit fails closed with actionable diagnostics |
| unsupported index mount | promotion or explicit rejection; never silent split |

The Step 23 probe is considered fixed only when its result becomes:

```text
overlay_alias_read: new
reader_parents: [writer_step]
after_commit.same_inode: true
after_commit.first_content: new
after_commit.alias_content: new
```

## 14. Performance evaluation

Record the cost separately for:

- session-start alias scan;
- first copy-up of a linked object with index on;
- per-step statx/catalog update;
- topology snapshot bytes and inode count;
- rollback normalization;
- object-aware commit and WAL backup.

Use group sizes 2, 8, 64, and 1024; file sizes 0 B, 4 KiB, 1 MiB, and 1 GiB;
and both complete and external/partial groups. Compare `index=off` only as an
incorrect baseline, `index=on`, promotion mode, and bare POSIX execution.

## 15. Non-goals and remaining boundaries

This design does not by itself solve:

- bind-mount aliasing, which requires mount-id/namespace-aware object roots;
- reflink/COW extent identity, which is not POSIX hard-link identity;
- atomic visibility of a multi-object host commit to unrelated external
  processes;
- hard links to directories, which Linux does not normally permit;
- aliases outside authorized roots unless the policy explicitly expands scope.

It does close the concrete Step 23 failure without prematurely replacing the
entire isolation substrate: the kernel preserves live hard-link semantics,
while AgentTX preserves the same object relation through dependency analysis,
rollback, commit, and crash recovery.

## 16. Current implementation status

The first implementation slice is now landed on the 244 branch:

- `third_party/try/try` and `scripts/bootstrap.sh` default to
  `TRY_OVERLAY_INDEX=on`; `off` remains an explicit historical baseline and
  `auto` resolves to `on` rather than silently disabling indexing;
- `LayerStore` copies carry a `(st_dev, st_ino)` memo, so upperdir copies and
  partial restores recreate later aliases with `link(2)`;
- `CommitWAL` carries the same inode memo across host pre-image and restore
  copies;
- upperdir fingerprints now include device, inode, and link count, exposing
  topology changes instead of treating equal bytes as equal objects;
- regression tests cover upperdir and WAL hard-link copies, and a root
  end-to-end probe confirms `index=on` returns `new` through the untouched
  alias while `TRY_OVERLAY_INDEX=off` returns the historical `old`.

This slice closes the live copy-up visibility failure and prevents the two
most direct snapshot/WAL alias splits.  The P0 selective-commit path is now
also landed for complete, host-visible groups:

- `src/agenttx/object_identity.py` scans the authorized workspace, expands a
  selected path to every alias in its `(st_dev, st_ino)` group, and fails closed
  when `st_nlink` proves that an alias is outside the workspace;
- `AgentTX._commit_path_plan()` expands both the selected prefix and the later
  suffix before conflict checking, so a later write through an alias cannot be
  hidden from the frontier planner;
- `SharedSemisolate.commit()` bypasses path-wise `try-commit` for those groups,
  writes the new bytes/metadata through one existing host inode, and removes
  only one name for a delete; ordinary paths retain the existing `try` path;
- `HardlinkCatalog` now persists a session object id, base identity token,
  alias set, link count, and completeness bit in `agenttx.json`; annotated
  effects carry that object id and the ledger treats equal object ids as RAW
  overlap even when the accessed names differ;
- the WAL receives the expanded alias set, so its existing inode memo restores
  the pre-commit topology; and
- when a later speculative suffix is retained, `rebase_upper_generation()` now
  reconstructs the unmounted upper through the inode-memo copier and strips
  volatile OverlayFS origin/index xattrs before the next mount;
- `probe_hardlink_alias.py` now defaults to the identity-focused no-trace mode
  and reports `new/new`, `same_inode=true`, `nlink=2` after a selective commit.

The remaining boundaries are unchanged for full topology publication: the
retained-upper generation rebase is now implemented, but groups whose topology
is created or
changed only inside the speculative overlay (new `link`, rename over, merge,
or an alias outside the authorized workspace) still fail closed or require a
future topology-aware path.  This slice therefore claims
correctness only for complete pre-existing regular-file groups with an
in-place content update or single-name unlink.
