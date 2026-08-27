# L1 Structure: AgentTX | OSDI 12-page draft | 2026-08-18

This outline instantiates the L0 thesis: a long agent trajectory is the
speculation unit, while a causal subgraph of versioned effects is the recovery
unit.  It follows the current separation between Background and Observation
and Motivation.  Sections 4 and 5 use the model--implementation boundary shown
by the supplied OSDI reference: Section 4 defines semantics, and Section 5
realizes them with Linux mechanisms.

## Page budget

| Section | Target pages | Contract |
|---|---:|---|
| Abstract | 0.25 | Problem, recovery-unit gap, AET, AgentTX, two strongest results |
| 1. Introduction | 1.25 | Multi-step agents, gap, example, three challenges, solution, evidence |
| 2. Background | 0.75 | Stateful workspaces and native recovery units |
| 3. Observation and Motivation | 1.25 | Execution evidence, recovery evidence, and resulting challenges |
| 4. Agent Effect Transactions | 1.50 | Abstract state, causal recovery, reconstruction, approved frontier |
| 5. AgentTX Implementation | 2.00 | Runtime path, capture, identity, reconstruction, publication, optimizations |
| 6. Evaluation | 3.50 | Semantics, cost, mechanism ablations, live agents, tokens, robustness |
| 7. Discussion and Limitations | 0.50 | Precise support boundary |
| 8. Related Work | 0.75 | Compare native recovery units |
| 9. Conclusion | 0.25 | Thesis, system, evidence, scope |
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

### 2--3. Background and Observation/Motivation

Keep background definitions separate, then let measurements drive motivation:

1. Define trajectory and READ, NEGATIVE, WRITE, DELETE effects.
2. Separate isolation from recovery granularity with Table 1.
3. Measure the execution path: per-call setup, shared execution, tracing tail.
4. Measure the recovery path: temporal retention and no-dependency removal.
5. Derive the design requirements and scope.

Keep no more than two main motivation figures in the final paper.  Merge the
optimization, scaling, and tail plots if space becomes tight.

### 4. Agent Effect Transactions

Follow one semantic chain:

1. Introduce the overview and four design principles: trajectory-level
   speculation, effect-based causality, versioned reconstruction, and separate
   publication.
2. Define $\langle V,L,H,F\rangle$, Append, Recover, Finalize, and the three
   invariants.
3. Define effect records, overlap, producer--consumer edges, and the descendant
   closure.
4. Define object-aware historical reconstruction and the retained-overlap rule.
5. Define the approved frontier, rolled-back holes, and publication boundary.

The section contains no strace, OverlayFS, upperdir, worker, or concrete WAL
details.  The overview figure is single-column and precedes the state model.

### 5. AgentTX Implementation

Follow the same chain using concrete mechanisms:

1. Build the shared four-directory OverlayFS view and preserve state across
   tool calls.
2. Combine syscall observations with upperdir differences and construct ledger
   records and cross-call edges.
3. Maintain historical versions and normalize supported object identities.
4. Plan, validate, and install a causal reconstruction in a fresh upper
   generation.
5. Publish an approved frontier with policy, alias-aware materialization, and a
   Prepare--Install--Finalize WAL.
6. Isolate persistent workers, incremental snapshots, and content-addressed
   blobs as performance engineering.

The per-call runtime figure is single-column at the start of this section.  Its
caption states that effect capture produces DAG input rather than the DAG
itself.

### 6. Evaluation

The final order should put semantics before performance:

1. RQ1: Does causal recovery retain valid work and remove invalid descendants?
2. RQ2: What do continuous isolation and dependency capture cost?
3. RQ3: Which capture, identity, reconstruction, and WAL mechanisms are needed?
4. RQ4: Can a live agent use recovery, and what replay does retention avoid?
5. RQ5: Do crash, reload, long-session, and concurrency tests preserve invariants?

Each RQ must state a hypothesis, sample count, validator, result, and boundary.
Token plots must pair token count with success.  A failed run with fewer tokens
is not a saving.

### 7 to 9

Discussion states unsupported topology, syscall coverage, external side effects,
and publication visibility.  Related Work compares native recovery units without
claiming unmeasured superiority.  Conclusion restates the semantic result and
filesystem-scoped boundary without adding a future-work list.

## Figure and table slots

| Slot | Artifact | Status |
|---|---|---|
| Table 1 | Recovery granularity | ready |
| Figure 1 | Motivation performance, scaling, tail | merge before final |
| Figure 2 | AgentTX system overview and causal recovery flow | ready (current draft Figure 4) |
| Figure 3 | Per-call runtime, effect capture, and snapshot flow | ready, single-column |
| Figure 4 | Causal retention and dependency ablation | ready |
| Figure 5 | Canonical x86 runtime distribution, 50 fresh workspaces/mode | ready |
| Table 2 | Syscall and object-identity coverage | missing |
| Figure 6 | Controlled avoided replay tokens | ready |
| Figure 7 | Token versus success on live tasks | needs repeats |
| Figure 8 | WAL, session, and concurrency robustness | ready within stated scope |

The detailed execution plan and evidence provenance are in
`docs/paper-outline-and-experiment-plan.md`.
