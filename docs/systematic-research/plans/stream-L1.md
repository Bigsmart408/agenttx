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
2. A standalone methodology subsection defines recovery completeness and
   collateral cost on the 400 paired manifests. It separately defines the
   native SWE-agent trace audit: one fixed-configuration record per task,
   with the full capture sweep preceding supplements; complete traces include
   verifier failures, and prefixes are reported separately. The retained
   capture audit has 258 complete SWE and 123 complete TB traces as of
   2026-11-01; each observed mutating step is a hypothetical root;
   observed-path dependencies; and temporal-suffix collateral work. Report
   zero-opportunity cases and distinguish this counterfactual from injected
   failures and measured replay tokens.
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
5. Observation 3 uses `FIG-Motivation-Native-Opportunity` to separate common
   cross-turn dependencies from rarer observed independent writes at risk in
   native traces. The five-turn threshold describes long edges but does not
   define recovery opportunity. The figure reports observed-path
   counterfactuals, not certified independence or measured replay savings.
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
2. **Real-world workloads.** Report SWE-Bench Lite and Terminal-Bench results
   side by side, keeping benchmark, harness, retry and accounting rules
   separate. Use live agents and official task tests plus recovery checks;
   oracle repair is not counted as task success. Cost comparisons retain
   failures/retries and require compatible, complete, matched-success pairs.
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
| Figure 4 | Native-trace recovery opportunity | interim full-sweep snapshot, single-column |
| Figure 5 | AET overview | ready, single-column |
| Figure 6 | AgentTX per-call runtime path | ready, single-column |
| Figure 7 | Analytical and full-system Microbenchmark on identical cases | analytical ready; 400-case system run missing |
| Figure 8 | SWE and TB success / matched cumulative token savings | ready, completed campaigns; distinct accounting scopes |
| Figure 9 | SWE and TB paired per-task token-ratio distributions | ready, retains unfavorable tails |
| Tables 2--3 | Real-workload outcomes / matched cost and pair counts | ready, complete-compatible cost pairs only |
| Component breakdown | Mechanism ablations | partial mechanism evidence ready |
| Tables 3--4 | Other overheads | runtime ready; storage/RSS/context scaling missing |
| Appendix tables | Correctness and interrupted recovery | semantic suite ready; interruption matrix missing |

The detailed execution plan and evidence provenance are in
`docs/paper-outline-and-experiment-plan.md`.

## Chinese first-draft contract — 2026-10-27

This scoped proposal follows the user's latest direction and supersedes the
older fresh-generation reconstruction wording for the Chinese draft only.
The English manuscript remains unchanged pending review of the Chinese text.

- Motivation bridge: chronological interleaving causes boundary-induced
  collateral loss and motivates graph-selected recovery. The motivation's
  oracle-localization condition stays in a separate experimental-method note;
  it is not a design assumption.
- Challenges: dependency discovery; object/version association; consistent
  non-prefix reconstruction of workspace and visible context. Each explains
  both correctness risk and the cost of needless invalidation.
- Section 4, design: interleaved effects and failure observation → graph
  evidence and boundaries → candidate recovery starts and forward closure of
  filesystem and message dependencies → historical object versions and shared
  survivor-state invariant. Candidate starts are plan choices, not known true
  causes. Explain fault localization separately from recovery-set selection.
  Keep Linux mechanisms out of the model. Use the existing formatter/report/
  independent-document example. One small conceptual DAG may illustrate it;
  no new raster figure is required for this Markdown draft.
- Section 5, implementation: one session-wide speculative workspace → per-call
  evidence, history, identities and message association → observe failure →
  separately explain candidate localization and plan verification → apply
  graph-selected recovery → selective historical version restoration →
  context alignment and continuation. No known-root normal-path assumption.
  End with brief persistent-worker/incremental-history optimizations.
- Evidence and scope: use the existing review findings; no fresh-generation
  validation/swap claim, arbitrary alias guarantee, automatic recovery on every
  nonzero tool exit, or unconditional retry after uncertain worker dispatch.
- Writing budget: approximately 1,200–1,800 Chinese characters per section,
  flexible for clarity, plus a short motivation/challenges bridge. These are
  workflow-level explanations, not API documentation. No new experimental
  numbers, invented results, publication protocol, or whole-paper rewrite.

Parallel section drafts are assembled into `paper/design-workflow.zh-CN.md`.
The challenge mapping and limited consistency audit are saved with the review
artifacts; whole-paper L3 completion is not claimed for a Chinese first draft.

## Approved English editing contract — 2026-10-27

Scope: directly replace Sections 4 and 5 in `paper/main.tex`, and align the
Introduction and Motivation challenge paragraphs. Preserve unrelated prose,
results, bibliography, figures, and section labels used by the rest of the
paper. The user approves this scoped workflow revision and requests execution.

- Section 4: boundary/dependency mismatch → overview and three goals → observed
  filesystem/message dependency graph with object relation → candidate-derived
  unified invalidation closure → historical reconstruction and matched context.
  Use an interleaved running example without assuming a known true root.
  The existing single-column overview remains; one short pseudocode figure may
  express unified closure/guarded reconstruction without a new LaTeX package.
- Section 5: continuous speculative execution and evidence capture → supported
  identity/version association → separate candidate localization and bounded
  isolated validation → affected-object restoration → visible-context handoff
  and continued execution → short overhead paragraph. Refer to Section 4 for
  semantics; do not repeat the model or enumerate function/field names.
- Three challenges remain dependency discovery, object/version association,
  and consistent non-prefix reconstruction. State why missing/extra edges,
  ambiguous objects and mismatched state cause either invalid survivors or
  unnecessary loss.
- Style: connected workflow paragraphs, no mount-directory inventory, syscall
  checklist, worker wire protocol, unsupported generation-swap promise, or
  hidden-provider-state restoration claim. No new root-known precondition.
- Validation: check the entire paper for claim/label continuity, validate the
  edited prose and algorithm against reviewed runtime paths, compile LaTeX and
  inspect changed pages if the available toolchain permits. Keep outstanding
  evaluation issues separate from the scoped design revision's result.

## Approved design-figure integration — 2026-10-29

The user asks to insert the accepted editable design figure into the manuscript
and explain it in prose. This is a scoped addition to Section 5, preserving the
existing thesis, Section 4 model, and Evaluation results.

- Place `figures/design-workflow/AgentTX-Design-Workflow.pdf` near the opening of
  Section 5 as a single-column figure, following the user's latest layout request.
- Explain try's mount / execution role and the reusable upperdir first, then
  use the A–F example to connect layer changes to filesystem and message edges.
- Distinguish the mounted lower / upper / merged views from per-step history
  stored outside the overlay. Do not imply a new mounted layer per tool call.
- Treat A as a candidate; its joint closure selects A/C/D/E and the affected
  objects f/h/p, preserving B/F and g/q. Explain version lookup, isolated
  verification, live selective reconstruction, and visible-context alignment.
- Retain existing upper restoration rather than a probe-image swap or baseline
  commit. Keep APIs, mount inventories, and syscall lists out of the prose.
- The user's subsequent color correction makes the top frame white and its
  small step frames uniformly white with blue outlines; semantic red / green
  remain only where state is selected or retained.

## Approved try-boundary clarification — 2026-10-29

The user approves adding the discussed clarification directly to the English
manuscript using the paper-writing skill. The existing thesis and section
chains remain in force; this is a scoped factual and workflow clarification.

- Background: distinguish a wrapped invocation (command, script, or agent
  session) from boundaries inside that invocation. Relabel try's table entry
  accordingly; identify the missing internal effect/history/message records.
- Implementation: tool dispatch supplies step boundaries; each exposed call
  binds execution, file effects, historical state, and native messages to one
  step identity. Preserve shared upper execution and the joint causal closure.
  A script submitted as one call remains one step. Explain matching visible
  context without claiming access to black-box internal history.
- Related Work: position AgentTX's contribution as dependency-selected recovery
  within a shared isolation boundary, rather than enabling session-long try.
- Align directly conflicting shorthand in Introduction and Motivation. Preserve
  all experimental data, citations, labels, and the single-column design figure.
- Validate edited prose against the existing implementation, synchronize the
  Section 5 draft, compile, and inspect the affected layout. Record a scoped L3
  audit; existing unrelated evaluation issues remain open.

## Approved real-workload figure and results revision — 2026-10-30

Scope: replace RQ2 with audited SWE and completed TB results; update directly
stale evidence limitations and the RQ2 question. Keep Design and its accepted
single-column illustration unchanged. Do not run new experiments or fill
unsupported RQ1/RQ3/RQ4 results.

Flow: separate campaign protocols and success definitions → overall outcomes
for every policy → compatible matched-success cumulative API-token cost →
task-level variation and unfavorable results → limitations of attribution.

- Figure 8: compact 2×3 grid, columns SWE/SWE-agent, TB/SWE-agent, TB/Codex;
  six-policy full-contract success bars above five-control token-saving bars.
  Preserve historical colors, Nimbus Roman, white frames and inward ticks.
  Include sample counts and 95% task-bootstrap intervals; TB resamples tasks
  within configuration groups. Overwrite the existing figure basename.
- Figure 9: same three columns of paired token-ratio ECDFs. Keep the common
  log range uncropped, with ratio one as the reference. Both favorable and
  unfavorable per-task costs remain visible.
- Tables: show all planned-task denominators, successful counts and matched
  pair counts. Do not pool benchmark rates or incompatible configurations.
- Results: explain conditional cost benefit without asserting improved
  fault localization or isolating the graph's effect from context policies.
  Include Codex's lower success count than branch rollback, higher Ideal
  token cost and ML/training reversals. State the retry and incomplete-usage
  restrictions beside results rather than leaving them only in an appendix.

Analysis lives in `review/realworld-results-20261030/`; a dedicated Section 6
agent drafts the replacement prose. Compile and inspect figure pages at paper
scale, then perform a scoped L3 consistency audit without declaring the whole
paper or the remaining evaluation complete.
