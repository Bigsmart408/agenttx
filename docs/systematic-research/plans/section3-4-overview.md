# AgentTX paper plan: Sections 3--5 in the current remote baseline

## Structural decision

The newly pasted remote version already separates background from observations.
Following the supplied OSDI reference, the paper should preserve the following
three-stage boundary:

- **Section 3: Observation and Motivation.** This section explains why command,
  session, branch, and checkpoint boundaries cannot preserve independent work.
  It ends with the three challenges and four requirements, but does not define
  the AET state machine.
- **Section 4: Agent Effect Transactions.** This section answers *what the
  recovery unit is* and *what must be true after each transition*. It is
  independent of `strace`, OverlayFS, upperdirs, workers, and WAL files.
- **Section 5: AgentTX Design and Implementation.** This section answers *how
  the model is realized at opaque tool boundaries*. It introduces the runtime
  data path, Linux identity limits, reconstruction procedure, and durable
  publication. Persistent workers and incremental snapshots appear here as
  performance engineering.

This avoids duplicating the new remote Background and Observation sections.
Section 4 should not repeat the measurements from Section 3, and Section 5
should not redefine AET semantics.

## Section 3: Observation and Motivation

Keep the current remote flow: stateful workspaces, native recovery boundaries,
execution overhead, recovery behavior, and the transition from temporal to
causal recovery. End the section with dependency discovery, object identity,
and selective reconstruction as the three challenges. Do not place the new
overview figure here; it belongs at the start of Section 4, after the motivation
has established why the causal recovery unit is needed.

## Section 4: Agent Effect Transactions: Model and Overview

### 4.1 Overview and design principles

Open with `FIG-AgentTX-Overview.png` as a double-column figure. Read the figure
from left to right and then bottom to top:

1. the agent chooses opaque tool calls;
2. `try` executes them in one shared OverlayFS speculative workspace;
3. AgentTX records effects and dependencies in metadata;
4. causal recovery removes the failed closure while retaining independent work;
5. an approved frontier is durably published to the host filesystem.

The prose should state four principles: trajectory-level speculation, effect
evidence instead of command-text inference, causal-closure reconstruction, and
separate recovery/publication transitions. This is the conceptual equivalent
of the reference paper's “Overview and Design Principles” subsection.

### 4.2 AET state model

Define the transaction as `\langle V,L,H,F\rangle`:

- `V`: the shared speculative filesystem view;
- `L`: tool steps, typed effects, parents, and status;
- `H`: historical versions needed to reconstruct affected objects;
- `F`: the finalized frontier that is allowed to become host-visible.

Define `Append`, `Recover`, and `Finalize`. State the host-cleanliness,
selective-preservation, and monotonic-frontier invariants. Do not mention how
the state is stored; that belongs in Section 4.

### 4.3 Effect DAG semantics

Define `READ`, `NEGATIVE`, `WRITE`, and `DELETE` effects and the abstract
`overlap` and object-identity relations. Give the producer/consumer and
negative-lookup dependency rules, then define the transitive descendant closure
of a failed step. Explicitly distinguish path overlap from object identity so
that the model does not promise more than the implementation can observe.

### 4.4 Selective reconstruction

Define the affected write/delete set and the version immediately preceding the
earliest selected effect on each target. State the fail-closed retained-overlap
rule and the post-recovery invariant: selected effects are invisible and every
non-overlapping retained effect remains visible. This subsection should answer
why causal rollback is not `undo(command)` or suffix restoration.

### 4.5 Commit frontier

Define the finalized boundary, rolled-back holes, and speculative suffix. Explain
that recovery changes the speculative view, while finalization materializes only
the approved frontier. WAL durability and host-install details are deferred to
Section 4.

## Section 5: AgentTX Design and Implementation

Start with one paragraph mapping the abstract tuple to concrete mechanisms:
shared OverlayFS realizes `V`, effect capture populates `L`, snapshots and the
object catalog provide `H`, and policy-checked WAL publication advances `F`.
Then follow the runtime data path.

### 5.1 Effect capture at tool boundaries

Explain upper-layer fingerprinting for writes/deletes and syscall observation
for reads/negative lookups. Define the trusted-declaration path and the
fail-closed behavior for trace or descriptor-resolution failures. Keep tracer
choice as an implementation detail; the semantics are the effect contract.

### 5.2 Shared speculative execution

Explain one shared `try -N` semisolate, cross-call visibility, host isolation,
and the persistent worker. State exactly what happens after worker death and why
the fallback does not change AET correctness.

### 5.3 Historical state and incremental snapshots

Explain `before_i`, immutable content-addressed blobs, incremental upperdir
replay, whiteouts, metadata, and reclamation. Tie these mechanisms to `H` and
to the reconstruction algorithm, not to the abstract invariant.

### 5.4 Object identity and alias boundary

Explain ancestor overlap, symlink normalization, rename as delete/create,
verified hard-link groups, OverlayFS `index=on`, and selective-commit alias
expansion. State the unsupported boundary plainly: bind mounts, external aliases,
and arbitrary generation changes fail closed.

### 5.5 Selective reconstruction implementation

Describe how AgentTX identifies affected paths, chooses historical versions,
checks retained overlap, rebuilds a fresh upper generation, and restores the
retained speculative suffix. This is the implementation counterpart of §3.4.

### 5.6 Durable commit, policy, and recovery

Describe the policy check before WAL preparation, then the `Prepare`, `Install`,
and `Finalize` phases. State what a crash before and after finalization means.
Keep the claim to crash-recoverable publication; do not claim external atomic
visibility across multiple host paths.

### 4.7 Performance engineering

Briefly collect persistent workers, incremental snapshots, and content-addressed
storage as optimizations. Their evaluation belongs in the runtime and mechanism
RQ, but their correctness role is only that they preserve the Section 3
invariants.

## Figure and cross-reference rules

- `FIG-AgentTX-Overview.png` is the overview figure (Figure 4 in the current
  draft, after the existing motivation figures) and appears at the beginning of
  Section 3, before the AET tuple is defined.
- The causal-retention plot remains in Evaluation; it is evidence, not a design
  diagram.
- The runtime flow in Section 4 should be described in the same order as the
  overview arrows. Avoid a second competing architecture figure.
- Section 3 must not cite implementation-specific experiment numbers. Section 4
  must not restate the motivation results; use forward references to Evaluation.

## Migration checklist

- [x] Replace the box-sketch lifecycle with the supplied overview figure.
- [x] Add a Section 3 opening that separates agent, runtime, and host boundaries.
- [x] Add a Section 4 opening that maps `\langle V,L,H,F\rangle` to mechanisms.
- [x] Split the current “Snapshots and Aliases” subsection into historical state
  and object identity.
- [x] Add a short explicit selective-reconstruction implementation subsection.
- [x] Keep the Evaluation RQ order unchanged: semantics, runtime, mechanisms,
  live agent/tokens, and robustness.
