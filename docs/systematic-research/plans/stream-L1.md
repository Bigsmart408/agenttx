# L1 Structure: AgentTX | OSDI 12-page draft | 2026-08-18

This outline instantiates the L0 thesis: a long agent trajectory is the
speculation unit, while a causal subgraph of versioned effects is the recovery
unit.  It reflects the merged Background and Motivation section in the current
LaTeX draft.

## Page budget

| Section | Target pages | Contract |
|---|---:|---|
| Abstract | 0.25 | Problem, recovery-unit gap, AET, AgentTX, two strongest results |
| 1. Introduction | 1.25 | Multi-step agents, gap, example, three challenges, solution, evidence |
| 2. Background and Motivation | 1.50 | Minimum vocabulary, execution evidence, recovery evidence, requirements |
| 3. Agent Effect Transactions | 1.50 | Abstract state, DAG, recovery invariant, publication frontier |
| 4. Design and Implementation | 2.00 | Capture, identity, reconstruction, publication, performance engineering |
| 5. Evaluation | 4.00 | Semantics, cost, mechanism ablations, live agents, tokens, robustness |
| 6. Discussion and Limitations | 0.50 | Precise support boundary |
| 7. Related Work | 0.75 | Compare native recovery units |
| 8. Conclusion | 0.25 | Thesis, system, evidence, scope |
| **Total** | **12.00** | References excluded |

## Flow by section

### Abstract

Long trajectories create provisional, interdependent effects.  Existing units
either expose them or discard causally independent work.  AET selects a causal
effect subgraph; AgentTX implements it.  Report the 144-run semantic result and
controlled avoided replay tokens.  Do not put preliminary GitHub-task results
or unverified repeated-performance data in the abstract.

### 1. Introduction

1. Establish coding agents as stateful, multi-step systems workloads.
2. Classify command, session, branch, checkpoint, and transaction boundaries.
3. Use one interleaved trajectory to show why time is not causality.
4. Derive Dependency Discovery, Object Identity, and Selective Reconstruction.
5. State the root cause: existing state is indexed by command, time, or path,
   while failure propagates through dependencies among versioned effects.
6. Introduce AET, then AgentTX, then bounded headline evidence.
7. End with problem, abstraction, implementation, and evaluation contributions.

### 2. Background and Motivation

Keep one background subsection and let the measurements drive the section:

1. Define trajectory and READ, NEGATIVE, WRITE, DELETE effects.
2. Separate isolation from recovery granularity with Table 1.
3. Measure the execution path: per-call setup, shared execution, tracing tail.
4. Measure the recovery path: temporal retention and no-dependency removal.
5. Derive the design requirements and scope.

Keep no more than two main motivation figures in the final paper.  Merge the
optimization, scaling, and tail plots if space becomes tight.

### 3. Agent Effect Transactions

Define AET as $\langle V,L,H,F\rangle$, then Append, Recover, and Finalize.
Describe overlap and object identity abstractly, compute the descendant closure,
state the reconstruction invariant, and define a monotonic frontier that permits
durably rolled-back holes.  Do not mention strace, OverlayFS, upperdir, worker
processes, or concrete WAL files in this section.

### 4. AgentTX Design and Implementation

Follow the correctness data path:

1. Capture writes from upperdir differences and reads or negative lookups from
   persistent tracing, with an explicit coverage gate.
2. Normalize hierarchy, symlink, rename, and supported hard-link identity;
   reject bind mounts, external aliases, and unsupported generations.
3. Reconstruct selected object versions and reject retained-effect overlap.
4. Apply policy before WAL preparation, then prepare, install, and finalize.
5. Describe persistent workers and incremental snapshots only as performance
   engineering.

### 5. Evaluation

The final order should put semantics before performance:

1. RQ1: Does causal recovery retain valid work and remove invalid descendants?
2. RQ2: What do continuous isolation and dependency capture cost?
3. RQ3: Which capture, identity, reconstruction, and WAL mechanisms are needed?
4. RQ4: Can a live agent use recovery, and what replay does retention avoid?
5. RQ5: Do crash, reload, long-session, and concurrency tests preserve invariants?

Each RQ must state a hypothesis, sample count, validator, result, and boundary.
Token plots must pair token count with success.  A failed run with fewer tokens
is not a saving.

### 6 to 8

Discussion states unsupported topology, syscall coverage, external side effects,
and publication visibility.  Related Work compares native recovery units without
claiming unmeasured superiority.  Conclusion restates the semantic result and
filesystem-scoped boundary without adding a future-work list.

## Figure and table slots

| Slot | Artifact | Status |
|---|---|---|
| Table 1 | Recovery granularity | ready |
| Figure 1 | Motivation performance, scaling, tail | merge before final |
| Figure 2 | AET lifecycle | replace box sketch |
| Figure 3 | Causal retention and dependency ablation | ready |
| Figure 4 | Canonical x86 runtime distribution, 50 fresh workspaces/mode | ready |
| Table 2 | Syscall and object-identity coverage | missing |
| Figure 5 | Controlled avoided replay tokens | ready |
| Figure 6 | Token versus success on live tasks | needs repeats |
| Figure 7 | WAL, session, and concurrency robustness | ready within stated scope |

The detailed execution plan and evidence provenance are in
`docs/paper-outline-and-experiment-plan.md`.
