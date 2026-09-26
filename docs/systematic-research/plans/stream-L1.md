# L1 Structure: AgentTX | OSDI 12-page draft | 2026-09-23

This outline instantiates the L0 thesis: a long agent trajectory is the
speculation unit, while a dependency-defined subgraph of versioned effects is the recovery
unit.  It follows the current separation between Background and Observation
and Motivation.  Sections 4 and 5 use the model--implementation boundary shown
by the supplied OSDI reference: Section 4 defines semantics, and Section 5
realizes them with Linux mechanisms.

## Page budget

| Section | Target pages | Contract |
|---|---:|---|
| Abstract | 0.25 | Problem, recovery-unit gap, AET, AgentTX, two strongest results |
| 1. Introduction | 1.25 | Multi-step agents, limitation, example, three challenges, solution, evidence |
| 2. Background | 0.75 | Stateful workspaces and native recovery units |
| 3. Observation and Motivation | 1.25 | Execution evidence, recovery evidence, and resulting challenges |
| 4. Agent Effect Transactions | 1.50 | Abstract state, causal recovery, workspace and context reconstruction |
| 5. AgentTX Implementation | 2.00 | Runtime path, capture, identity, workspace/context reconstruction, optimizations |
| 6. Evaluation | 3.50 | Microbenchmarks, real-world workloads, breakdown, and other overheads |
| 7. Discussion and Limitations | 0.50 | Precise support boundary |
| 8. Related Work | 0.75 | Compare native recovery units |
| 9. Conclusion | 0.25 | Thesis, system, evidence, scope |
| Appendix | excluded | Correctness matrix and interrupted-recovery tests |
| **Total** | **12.00** | References excluded |

## Flow by section

### Abstract

Long trajectories create provisional, interdependent effects.  Existing units
either expose them or discard causally independent work.  AET selects a
dependency-defined effect subgraph; AgentTX implements it.  Report the 144-run semantic result and
controlled avoided replay tokens.  Do not put preliminary GitHub-task results
or unverified repeated-performance data in the abstract.

### 1. Introduction

1. Establish coding agents as stateful, multi-step systems workloads.
2. Classify command, session, branch, checkpoint, and transaction boundaries.
3. Establish the correctness--retention tension without reporting Motivation
   data, then use one interleaved trajectory to show why time is not causality.
4. Explain that no fixed coarse boundary dominates, then derive Dependency
   Discovery, Object Identity, and Selective Reconstruction.
5. State the root cause: existing state is indexed by command, time, or path,
   while failure propagates through dependencies among versioned effects.
6. Introduce AET, then AgentTX, then bounded headline evidence.
7. End with problem, abstraction, implementation, and evaluation contributions.

### 2--3. Background and Observation/Motivation

Keep background definitions separate. Section 3 follows the same functional
sequence as an OSDI motivation study: path analysis, overhead analysis, then
attempts and insight.

1. Section 3.1 traces how root-only, turn-checkpoint, temporal-suffix, and
   whole-branch recovery select state. It uses Table 1 and one shared multi-turn
   example, but reports no benchmark percentages. The section first explains
   artifact availability: try, BranchFS, YoloFS, and Waypoint are measured
   natively, while
   unavailable or non-recovery systems are represented by boundary semantics.
   Whole branch uniformly represents whole-unit recovery rather than any named
   artifact. Every boundary baseline receives oracle localization and selects
   the smallest native units covering affected effects. It therefore gives the
   maximum retention expressible at that granularity.
2. Section 3.2 defines recovery completeness and collateral cost, then states
   the unified microbenchmark methodology. The 30 physical cases validate the
   benchmark; all three observations use the same 400 paired manifests.
3. Observation 1 establishes the correctness--retention dilemma with
   `FIG-Motivation-Suite` and defines complete affected-set removal as the gate
   for all efficiency comparisons. The figure includes matched 400-case
   artifact-native YoloFS and Waypoint results. Its turn result locates
   substantial collateral loss inside affected turns.
4. Observation 2 identifies chronology--dependency mismatch with
   `FIG-Motivation-Dependency`. It explains why turn/checkpoint recovery cannot
   represent non-prefix state and motivates tool steps as the recorded unit.
   The same 210 dependency-sweep manifests include artifact-native try,
   BranchFS, YoloFS, and Waypoint recovery. Each artifact is named directly;
   coincident state selections use distinct markers.
5. Observation 3 shows that no fixed coarse boundary dominates with
   `FIG-Motivation-Boundary-Crossover`. Temporal-step recovery demonstrates
   that recording a finer unit is insufficient while selection remains a
   temporal suffix.
6. Section 3.3 explains why incremental boundary changes remain insufficient,
   distinguishes step-level recording from temporal step rollback, states the
   speculation-unit/recovery-unit insight, and derives dependency discovery,
   object identity, and non-prefix reconstruction as design challenges.

Keep the three observation figures independent. The separate
`FIG-Motivation-Coarse-Waste` reports controlled task-length, chronology, and
rollback-amplification trends; do not merge it with the observation figures.

### 4. Agent Effect Transactions

Follow one semantic chain:

1. Introduce the overview and four design principles: trajectory-level
   speculation, effect-based causality, versioned workspace reconstruction, and
   context-aligned rollback.
2. Define $\langle V,L,H,M\rangle$, Append, Recover, and the workspace/context
   invariants.
3. Define effect records, overlap, filesystem and message-flow edges, and their
   unified fixed-point descendant closure.
4. Define object-aware historical reconstruction and the retained-overlap rule.
5. Define how the same recovery set reconstructs the workspace, removes invalid
   message spans, and rebuilds model-visible context.

The section contains no strace, OverlayFS, upperdir, worker, or provider-specific
message APIs. The overview figure is single-column and precedes the state model.

### 5. AgentTX Implementation

Follow the same chain using concrete mechanisms:

1. Build the shared four-directory OverlayFS view and preserve state across
   tool calls.
2. Combine syscall observations with upperdir differences and construct ledger
   records and cross-call edges.
3. Maintain historical versions and normalize supported object identities.
4. Plan, validate, and install a causal reconstruction in a fresh upper
   generation.
5. Rewind the conversation log from the same recovery set and append an
   authoritative retained/invalidated-state notice.
6. Isolate persistent workers, incremental snapshots, and content-addressed
   blobs as performance engineering.

The per-call runtime figure is single-column at the start of this section.  Its
caption states that effect capture produces DAG input rather than the DAG
itself.

### 6. Evaluation

The final order follows evidence strength rather than implementation order:

1. **Microbenchmark.** First report the controlled effect-DAG sweep, then run
   the full system on the exact same 400 Motivation manifests. The analytical
   view explains the mechanism; the full-system view measures end-to-end
   success, retained work, total tokens including replay, and recovery time.
2. **Real-world workloads.** Report complete SWE-Bench Lite and Terminal-Bench
   results separately, then aggregate. The primary result uses live agents and official verifiers;
   oracle repair is not counted as task success.
3. **Breakdown.** Remove dependency capture, object identity, selective history,
   and context rewind one at a time on the same 400 Microbenchmark cases.
   Separate semantic ablations from persistent-worker and compact-context
   performance variants.
4. **Other overheads.** End with tracing, history storage, RSS, ledger/context
   indexing, planning, reconstruction, handoff, startup, and reload costs.

Each subsection must state a hypothesis, sample count, validator, result, and
boundary. Token plots pair token count with success and include replay. A failed
run with fewer tokens is not a saving. Missing canonical results remain marked
as missing rather than being replaced with smoke or oracle-repair results.

### Appendix: Correctness and Interrupted Recovery

Move the effect/identity topology matrix, repeated-recovery checks, reload
checks, and workspace--context consistency suite to the appendix. Add recovery
interruption injection before reconstruction, before workspace swap, between
workspace and context updates, before metadata persistence, and before the
response. A reload may expose the complete pre-recovery or post-recovery pair,
but never a mixed workspace/context state. This evaluates interrupted rollback,
not publication to the user workspace.

### 7 to 9

Discussion states unsupported topology, syscall coverage, external side effects,
and the exclusion of result publication. Related Work compares native recovery units without
claiming unmeasured superiority.  Conclusion restates the semantic result and
filesystem-scoped boundary without adding a future-work list.

## Figure and table slots

| Slot | Artifact | Status |
|---|---|---|
| Table 1 | Recovery granularity | ready |
| Figure 1 | Controlled coarse-recovery scaling | ready, single-column |
| Figure 2 | Correctness--retention tradeoff over 400 paired manifests | ready, single-column |
| Figure 3 | Dependency-value sweep | ready, single-column |
| Figure 4 | Coarse-boundary crossover | ready, single-column |
| Figure 5 | AET overview | ready, single-column |
| Figure 6 | AgentTX per-call runtime path | ready, single-column |
| Figure 7 | Analytical and full-system Microbenchmark on identical cases | analytical ready; 400-case system run missing |
| Figure 8 | Full SWE-Bench Lite / Terminal-Bench real-world results | full live-agent run missing |
| Figure 9 / Table 2 | Component breakdown | partial mechanism evidence ready |
| Tables 3--4 | Other overheads | runtime ready; storage/RSS/context scaling missing |
| Appendix tables | Correctness and interrupted recovery | semantic suite ready; interruption matrix missing |

The detailed execution plan and evidence provenance are in
`docs/paper-outline-and-experiment-plan.md`.
