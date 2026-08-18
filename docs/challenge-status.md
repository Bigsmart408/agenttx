# AgentTX challenge status and implementation audit

This audit reconciles `docs/research-challenges.md` with the current 244
implementation.  “Complete” means covered by the maintained runtime and a
regression or end-to-end probe; “partial” means the supported subset is
implemented and unsupported cases fail closed.

| Challenge | Current status | Implemented evidence | Remaining boundary |
|---|---|---|---|
| Dependency Discovery | **Partial, supported path complete** | Tool boundaries record `READ`, `NEGATIVE`, `WRITE`, and `DELETE`; the ledger derives producer--consumer edges, including hierarchy, symlink requests/resolved paths, and persisted hard-link object IDs. Both persistent `strace` and persistent eBPF backends are selectable. The eBPF parser now raises a coverage error for relative non-`AT_FDCWD` paths unless a kernel-resolved path is present. | Trace coverage is not universal. Unsupported syscalls and mount-namespace aliases still need a broader coverage contract; unresolved descriptor paths fail closed rather than becoming guessed edges. |
| Object Identity | **Partial, pre-existing hard-link groups complete** | `HardlinkCatalog` persists object IDs and alias sets; complete host-visible regular-file groups are expanded for dependency planning and WAL. Indexed OverlayFS preserves live copy-up visibility. The current commit path also handles tested overlay-created hard-link groups and intentional rename-over splits. | Bind mounts, aliases outside authorized roots, ACL/capability-complete topology, reflink/COW extent identity, and arbitrary multi-object topology publication remain fail-closed. |
| Selective Reconstruction | **Implemented for supported file topology** | Causal closure selects non-contiguous failures; per-step upperdir versions reconstruct selected paths; retained-effect overlap fails closed; WAL restores interrupted publication; retained speculative suffixes are rebased to a fresh upper generation. | WAL provides durable recovery, not externally atomic visibility to unrelated observers. Full topology-aware multi-object commits and large-tree traversal costs remain open engineering work. |

## Changes landed for this audit

1. The introduction now presents the three challenges as a single recovery-unit
   mismatch and explicitly separates correctness mechanisms from persistent-worker
   and incremental-snapshot performance optimizations.
2. The object-identity implementation is documented as a bounded contract: the
   runtime claims complete pre-existing regular-file groups and tested
   overlay-created/rename-over cases, while refusing unverified bind or external
   aliases.
3. The paper reports the measured effect of the solutions: 100% retention of
   independent work and removal of invalid descendants in 144 controlled runs,
41% retention for temporal rollback at 64 calls, and 1,424.7--3,340.3 avoided
   replay tokens at 48 document entries.  These are avoided replay tokens, not
   total LLM usage.

## Next implementation targets

The descriptor trace-coverage gate is now landed: until the kernel supplies a
resolved path (or an explicit structured declaration is provided), the runtime
fails closed.  Remaining implementation work is broader syscall and
mount-namespace coverage.  A kernel-level atomic multi-object publication
mechanism and bind-mount topology support are intentionally not claimed by the
current paper.
