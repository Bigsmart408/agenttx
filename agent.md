# AgentTX Experiment Specification

[中文版本](agent.zh-CN.md) · [当前实验进度（中文）](docs/EXPERIMENT_STATUS_20261017.md)

**Local execution update, 2026-10-17.** All code changes, environment preparation,
validation and future experiments run on local host `r760-ssd`. The remote
machine is a reference-code source only; its preparation queue has stopped.
The earlier remote image and TB checks are historical diagnostics and do not
count as local readiness. Code remains in this project; Docker image layers,
cache, results, logs and temporary files use `/mnt/data/agenttx-runtime`.
Source `scripts/agenttx-local-env.sh` to select the dedicated local Docker
socket and data paths. The shared system daemon is not restarted. Local
Alpine pull, offline runtime, Docker build/runtime and 18 relevant regression
tests passed. Existing recovered cache/log/result data is moved with content
verification before switching the original project paths to symlinks.

Only environment preparation is authorized at this stage. The local queue
verifies 300 SWE images with two workers and hydrates pinned TB inputs into
the data cache. Full task readiness still requires pristine base-commit
source, dependencies and official verifier checks. Preserve all failed and
interrupted attempts; no model launch or result-based retry is automatic.
Current local progress is maintained in the
[environment preparation record](docs/DOCKER_ENVIRONMENT_20261017.md).

**Image pull deadlines, 2026-10-17.** Environment cohort r3 replaces r2's
30-minute total cutoff with Docker API byte/milestone monitoring: fail over
after 900 seconds without download/extraction progress, or 14400 seconds
total per source; explicit registry/transport errors fail over immediately.
Sources are `docker.1ms.run`, `hub.rat.dev`, then `docker.1panel.live`; no direct
upstream fallback. A real blocked SWE image's manifest/config and large-layer
sample were checked across candidates; all three usable sources returned the
same manifest digest. This is a reachability sample, not a completed large
image pull. Seven timeout/progress tests and a local API pull/offline-container
smoke check passed. Existing cached images are reverified, not redownloaded;
all 1818 pinned TB files and SWE metadata were rechecked locally. Preserve r2
with 121 ready images, 9 errors and 2 pulls interrupted deliberately at the
cutover; record the interruptions separately, without changing old outcomes.
The new rules apply to environment downloads only; model budgets are unchanged.
See [timeout diagnosis and evidence](docs/IMAGE_PULL_TIMEOUT_20261017.md).

**Earlier launch, now paused.** The new
[stratified pilot](experiments/plans/stratified_pilot_20261017/registration.json)
is launched on the remote validation host with **8 concurrent experiment
processes**. It first runs 84 model-free runtime gates (12 canonical manifests
across all three families × 7 policies). All gates must pass before the queued
168 model cells (12 × 7 × 2 harnesses, one repeat) begin. Both Codex and
SWE-agent use `gpt-5.6-luna / max`, a common 900-second model wall limit,
no model-call cap, and a localization probe limit of `2 * operation_count + 8`
fixed before execution. This is a separate diagnostic cohort from the earlier
240-second repeats: token-budget enforcement and matched template accounting
remain formal gates. Preserve every failure and timeout; do not retry by
outcome. The frozen v5 execution source is
`938a059fa1337a8f2e069bc70de1edb8e0251e6d90155a460320690393c78bef`.
Live snapshots and completion evidence are linked in the
[pilot progress report](docs/STRATIFIED_PILOT_20261017.md).

The complete system microbenchmark covers **both harnesses**: 400 manifests ×
7 policies × 3 repeats × 2 harnesses = **16800 cells**. The older 8400-row
registration has no harness dimension and remains an immutable historical
plan. Real-world scope remains 5410 cells (300 SWE + 241 TB, five policies,
both harnesses); pilot results do not fill that formal matrix.

This document is the authoritative experiment specification. The evidence must
be produced and presented in this order:

```text
Microbenchmark
    -> Real-world workloads (SWE-Bench Lite and Terminal-Bench)
    -> System breakdown
    -> Appendix correctness and interrupted-recovery tests
    -> Other overheads
```

No layer substitutes for another. The microbenchmark combines analytical
sweeps with complete, reproducible long agent trajectories; the two official
suites provide real-world repository and shell workloads; the breakdown
attributes the result to specific mechanisms; correctness tests establish
invariants; and overhead experiments bound costs not visible in end-to-end
completion metrics.

The existing 400-case Motivation suite remains the controlled Microbenchmark
layer. A separate native-trajectory audit may establish whether its recovery
opportunity occurs in real agent work; it must keep its own sampling frame and
denominators. The earlier partial 541-task runs remain audit data, not completed
real-world results. Final paper results must be regenerated from a frozen
revision under the protocols below.

# Part I: Motivation Microbenchmark

## 1. Goal

Measure how recovery granularity behaves across a branch containing multiple
agent turns. The benchmark sweeps both `dependency_value` and task length,
represented by `turn_count=1..10`, rather than selecting one sparse,
single-turn graph that maximizes the ideal oracle's gap.

This is a controlled microbenchmark, analogous to sweeping thread count in a
systems benchmark. It establishes the mechanism behind the motivation claim:

> Temporal, turn-level, and branch-level rollback waste work when chronology is
> a poor proxy for causal dependence; the ideal selective rollback bound
> quantifies how much valid work a precise recovery mechanism could preserve.

No SWE-Bench or Terminal-Bench run is required to regenerate the controlled
Motivation figures. The proposed real-trajectory incidence figure draws on
these suites separately; their end-to-end policy results remain in Part II.

## 2. Motivation Argument and Unified Microbenchmark

The three recovery-boundary observations use one controlled microbenchmark
family. Native-trace incidence may follow them as separately labeled
external-validity evidence, but its cases, denominators, and token-weight
proxies must not be pooled with controlled manifests or real-model tokens.
The controlled claim is:

> Existing recovery boundaries follow execution structure (time, turn, or
> branch), while corruption follows dependencies. Under-rollback leaves
> invalid descendants; correct coarse rollback removes valid independent work.
> No fixed coarse boundary dominates as task length, fault position, and
> dependency structure change.

All controlled experiments use the same deterministic filesystem-effect
generator, canonical policy definitions, paired manifests, and validators.
The native-trajectory audit instead uses observed tool effects. The ideal
oracle is an analytical upper bound, not an implemented system. Motivation
must not run, plot, or claim results for `agenttx_causal`.

### 2.0 Motivation writing outline (authoritative)

The narrative structure is adapted from the evidence flow in
[WOLVES (OSDI '25)](https://www.usenix.org/conference/osdi25/presentation/pan):
analyze the existing paths, measure and break down their cost, state the
significance, explain why incremental attempts remain insufficient, then derive
the key insight and design requirements. We borrow this argumentative
progression, not its file-system claims or terminology.

The section must answer one central question:

> Why can no recovery boundary defined only by execution structure provide
> both complete recovery and low collateral loss for a multi-turn agent?

The three observations are not parallel facts. They form the following causal
chain:

```text
coarse boundaries can recover correctly only by over-deleting
    -> because temporal order does not encode failure dependence
    -> therefore choosing or tuning one fixed coarse boundary cannot solve it
    -> recovery needs a dependency-defined unit inside a long-lived trajectory
```

#### 3.1 Analyzing Recovery Paths of Stateful Agents

Open with one short paragraph and the recovery-boundary table from Background.
Group existing mechanisms by the state they can restore: failed step, turn,
temporal suffix, or whole branch. State the shared limitation neutrally: these
boundaries are defined by execution structure, not by which later effects were
actually influenced by the failure. Do not introduce the proposed system or
claim that causality is already the answer.

This subsection plays the same role as the reference paper's I/O-path analysis:
before measuring overhead, trace how each representative mechanism reaches its
recovered state. Follow one shared multi-turn example and show the selected set
for Root only, Turn checkpoint, Temporal step, and Whole branch recovery. Use
the ideal set only as a reference target. Explain what each path preserves and
what it necessarily cannot distinguish; do not report benchmark percentages in
this subsection.

The current `paper/main.tex` subsection **Recovery Boundaries in Stateful Agent
Execution** belongs here. It must be moved before the experiments, not left
after Observations 1--3. Its existing-boundary and state-propagation paragraphs
should be condensed into this path analysis; its design requirements belong in
Section 3.4 below.

#### 3.2 Analyzing Recovery Overheads

This subsection corresponds to the reference paper's crash-consistency
overhead analysis. It first states the methodology, then reports a breakdown,
three observations, and their significance. Every result is a consequence of
the paths defined in Section 3.1.

Define the two measured axes here, rather than in the path analysis:

1. **Recovery completeness:** what fraction of failure-affected effects is
   removed?
2. **Collateral recovery cost:** how much valid, independent work is discarded
   once completeness is satisfied?

This distinction prevents `no_recovery` or `root_only` from appearing efficient
merely because they leave corrupted descendants behind.

##### Methodology: one controlled workload family and paired population

Describe the multi-turn microbenchmark once: 16 operations per turn; 1--10
turns; controlled fault position, dependency value, graph shape, and
interleaving; identical manifests and fault roots for all six policies.

All three main observations and every later full-system Microbenchmark
experiment use the same 400 paired manifests as their statistical population:

- 100 task-length cases;
- 90 fault-position cases;
- 210 dependency-value cases.

The paper should state this 400-case population once and reuse it for all
three controlled observations. A subsection may zoom into one registered sweep
to explain a mechanism, but it must return to the same suite-level population
and must not present that slice as a new workload. The observational native-
trajectory audit in Section 2.8 has a different denominator and cannot be
reported as another slice of these 400 cases.

##### Observation 1: correctness creates a collateral-loss dilemma

**Claim.** Correct coarse recovery removes the invalid closure but also
discards useful work; low-cost under-recovery is not a valid alternative.

**Evidence.** Begin with the correctness gate over the shared suite. No recovery
removes 0% of the invalid closure and Root only removes 17.03% on
average. Turn checkpoint, Temporal step, Whole branch, and Ideal oracle all remove
100%. Among these correctness-qualified policies, Turn checkpoint, Temporal step, and
Whole branch discard 54.02%, 56.90%, and 100% of valid work on
average, while Ideal oracle discards none. Use the task-length sweep only to make the
amplification concrete: as a branch grows around a fixed four-operation
closure, coarse rollback grows while the exact target does not.

**Significance.** Existing boundaries face a correctness--retention dilemma:
rolling back less is incorrect, while rolling back safely may erase most of a
long-running agent's useful progress.

**Figure role.** `FIG-Motivation-Suite` is the independent Observation 1
figure. It shows the correctness gate and collateral-cost breakdown over the
400 paired cases. `FIG-Motivation-Coarse-Waste` remains a separate controlled-
scaling figure; do not merge it with the three observation figures. In the
paper, keep `FIG-Motivation-Suite` within one column and arrange its two internal
panels horizontally.

##### Observation 2: chronology--dependency mismatch is the root cause

**Claim.** The waste is not an incidental property of one checkpoint policy;
it arises because failure propagation follows dependencies while coarse
recovery follows time, turns, or branches.

**Evidence.** Introduce `dependency_value` only here, after Observation 1 has
established the symptom. Across the shared 400 manifests, 390 recovery targets
(97.5%) are non-prefix states. In the registered dependency sweep, temporal
recovery approaches the exact bound only as dependency value approaches one;
when dependencies are sparse, independent and affected work interleave and
temporal rollback discards the independent effects between them.

**Significance.** An exact recovered state often never existed as a temporal
checkpoint. Faster checkpoint creation, denser checkpoint placement, or a
cheaper branch abort can reduce mechanism overhead, but cannot represent that
non-prefix state.

**Figure role.** `FIG-Motivation-Dependency` is the root-cause figure. Its
x-axis is dependency value and its lines show discarded useful work, retained
valid work, and rollback amplification. Every statement about the mechanism
must be tied to a visible trend in this figure. Keep the figure within one
column and keep all internal panels horizontally aligned.

##### Observation 3: selecting a different fixed boundary does not solve it

**Claim.** No static coarse boundary is consistently best across workload
structures.

**Evidence.** Reuse the same 400 cases. Turn checkpoint has lower attributed
waste in 139 cases, Temporal step in 251, and they tie in 10. The ranking
reverses by registered slice: Turn checkpoint wins 60/100 task-length and 60/90
fault-position cases, while Temporal step wins 191/210 dependency cases. The best
fixed boundary still discards 10.5% more useful token weight than the post-hoc
per-case minimum of those two coarse choices.

**Significance.** The result rules out the obvious incremental response to
Observation 2: picking a globally better checkpoint or suffix policy. Boundary
selection changes which independent work is lost, but no fixed execution
boundary expresses the desired recovery set.

**Figure role.** `FIG-Motivation-Boundary-Crossover` is the limitation figure.
It reports both rank reversals and fixed-policy cost. The per-case minimum is a
diagnostic coarse-policy envelope, not the ideal oracle and not an implemented
adaptive policy. Keep the figure within one column and arrange its two internal
panels horizontally.

End Section 3.2 with one **Significance** paragraph that combines the three
observations without repeating their numbers: correct coarse recovery wastes
work, the waste is structural rather than an implementation artifact, and no
fixed coarse boundary removes the tradeoff. Do not introduce the solution in
this paragraph.

The current `paper/main.tex` subsection **Recovery Microbenchmark** becomes
Section 3.2. Move its recovery-policy definitions to Section 3.1, keep its
workload and measurements here, and remove the standalone suite-level detour
between Observations 1 and 2.

#### 3.3 Real-Trajectory Incidence and Case Anatomy

After the controlled observations, present the separately sourced audit in
Section 2.8. It asks whether real agent executions contain independently
useful work that a correct coarse rollback would discard. State the full task,
trajectory, turn, and candidate-fault denominators before any percentage.
Show zero-opportunity cases and one positive and one negative trace example.
Call the counterfactual result a *retention opportunity*, not an observed
crash, realized token saving, or measured AgentTX result. If the eligible
sample stays small, move the analysis to an appendix and narrow the main-text
claim rather than extrapolating from the six-turn pilot.

#### 3.4 Motivation: Attempts, Insight, and Design Challenges

Close the section in the same order as the reference paper's motivation:

1. **Attempts.** Root only recovery preserves work but fails completeness.
   Turn checkpoint constrains loss to affected turns but cannot retain valid
   work within them. Temporal step is exact only when all later work
   depends on the fault. Whole branch is safe but maximally coarse.
   Increasing checkpoint frequency changes cost, not representability.
2. **Insight.** The lifetime of speculation and the unit of recovery are
   different system decisions. Keep the trajectory as the speculation unit,
   but define the recovery unit by affected effects rather than by one
   contiguous execution interval.
3. **Design challenges.** State the non-trivial questions implied by the
   insight: (i) how to discover dependencies hidden inside opaque tool calls
   and descendant processes; (ii) how to track object identity and historical
   versions across aliases, renames, replacements, and copy-up; (iii) how to
   reconstruct a non-prefix state consistently across workspace and
   agent-visible context; and (iv) how to publish only an approved durable
   frontier without exposing a speculative suffix.
4. **Transition.** End by stating that the next section introduces an
   abstraction addressing these challenges. Do not name AgentTX or describe
   implementation mechanisms inside Motivation; the system name belongs at
   the start of the following design section.

##### Section flow and figure order

The intended main-text order is:

```text
3.1 trace existing recovery paths (no performance claims)
    -> 3.2 unified methodology and suite-level correctness gate
       -> Observation 1 + correctness/collateral-cost figure
       -> Observation 2 + dependency-sweep figure
       -> Observation 3 + boundary-crossover figure
       -> combined significance
    -> 3.3 separate real-trajectory incidence + positive/negative case anatomy
    -> 3.4 attempts -> insight -> design challenges -> transition
```

Keep the three observation figures independent. Do not compress
`FIG-Motivation-Suite`, `FIG-Motivation-Dependency`, and
`FIG-Motivation-Boundary-Crossover` into one composite. The separate
`FIG-Motivation-Coarse-Waste` figure documents controlled task-length,
chronology, and rollback-amplification trends before the observations;
one-factor sensitivity plots remain supporting material.

### 2.1 MB1: Coarse-boundary scaling

**Question.** If the true fault closure stays small while the branch grows from
one to ten turns, how much unnecessary work does each correct policy remove?

**Input.** Sweep 1--10 turns with 16 operations per turn. Fix the fault at 25%
of the complete branch and its invalid closure at four operations. Use 10 paired
manifests per turn count and keep the full policy manifest for audit, but
exclude unsafe controls from efficiency claims.

**Figure 1a/1c.** Sweep 1--10 turns at 16 operations per turn and report valid
operations discarded and rollback amplification. The fault closure remains
four operations, so growth is caused by task length rather than fault size.

### 2.2 MB2: Chronology sensitivity

**Question.** Does a temporal policy change when dependencies remain fixed but
the fault moves through execution order?

**Input.** Hold task length at 10 turns, turn width at 16, and closure size at
four. Sweep global fault position from 0.10 to 0.90 with 10 paired manifests
per point.

**Figure 1b.** Report valid operations discarded. Turn checkpoint follows the
turns containing invalid effects, Whole branch follows the complete branch,
Ideal oracle remains exact, and Temporal step waste varies with the suffix. This isolates
chronology from dependency structure.

### 2.3 MB3: Dependency-value sweep

**Question.** How does the mismatch change as causal density ranges from sparse
to dense?

**Input.** Use five turns of 16 operations, a 25% global fault position,
layered shape, alternating interleaving, all 21 `dependency_value` points from
0.00 to 1.00, and 10 paired manifests per point.

**Figure 3.** Plot useful work discarded, valid work retained, and rollback
amplification for all four correct policies. Include every preregistered point,
including dense cases where Temporal step approaches Ideal oracle.

### 2.4 Fixed-boundary crossover

**Question.** Can one coarse recovery boundary remain the better choice as
task structure changes?

**Input.** Reuse exactly the 400 paired MB1--MB3 manifests. Do not generate a
new workload or select a favorable subset. For each manifest, compare
`turn_checkpoint_proxy` and `temporal_step` by `useful_tokens_wasted`, report
which policy has lower waste, and retain ties.

**Figure 4.** Report lower-waste policy frequency separately for the task-
length, fault-position, dependency-value, and combined slices. Also report the
mean waste of each fixed policy and the post-hoc per-case minimum of the two.
The per-case minimum is an analytical coarse-policy envelope, not a realizable
oracle and not an additional recovery policy.

### 2.5 Supporting sensitivity and breakdown

One-factor sweeps over turn count, fault position, graph shape, and interleaving use
the same generator and policy semantics. They may appear in the main text when
they support a distinct claim; otherwise they belong in the appendix. The
suite-level summary combines all 400 paired manifests from MB1--MB3: 100
turn-count cases, 90 fault-position cases, and 210 dependency-value cases. It reports
recovery completeness for all policies, then measures collateral recovery cost
only for policies that remove the complete invalid closure.

Native-trace incidence may support Motivation only under the separate
observational protocol in Section 2.8. Real-model replay, SWE-Bench and
Terminal-Bench end-to-end policy results belong in Evaluation. None may be
pooled with the 400 controlled manifests or used to replace their sweeps.

### 2.6 Main-text figure disposition

There is no fixed upper bound on the number of Motivation figures, but the main
text should follow the three-observation chain in Section 2.0. The current
artifacts map to it as follows:

1. **Correctness--retention dilemma:** use the suite-level correctness and
   collateral-cost results from `FIG-Motivation-Suite` as Observation 1. Use
   `FIG-Motivation-Coarse-Waste` for controlled task-length, chronology, and
   rollback-amplification trends.
2. **Temporal--dependency mismatch:** MB3 in `FIG-Motivation-Dependency`,
   supporting Observation 2. Across MB1--MB3, 390/400 exact survivor states
   cannot be represented by one temporal-prefix checkpoint, demonstrating how
   often execution order diverges from failure dependence.
3. **Coarse-boundary crossover:** the same 400 MB1--MB3 manifests in
   `FIG-Motivation-Boundary-Crossover`, supporting Observation 3 by showing
   that Turn checkpoint and Temporal step recovery reverse rank across the
   registered workload slices.
The one-factor turn-count, fault-position, shape, and interleaving sweeps remain
available in `FIG-Motivation-Sensitivity` as supporting robustness evidence,
but they are not a separate main-text observation.

`FIG-Motivation-Suite` and `FIG-Motivation-Coarse-Waste` remain separate:
the former supports Observation 1, while the latter shows controlled scaling.
Additional main-text figures may
report another controlled view only when it supports a distinct observation.
The real-trajectory incidence figure, if the Section 2.8 acceptance gates
pass, is a separately sourced external-validity figure after Observation 3,
not a fourth controlled observation or a panel of the 400-case figures.
`FIG-M1-Strategy-Outcomes` is a historical, noncanonical plot and
`FIG-M2-Extreme-Tradeoff` is a historical rendering of the same MB1 endpoint,
so it must not be counted as additional evidence.

### 2.7 Current data status

| evidence | protocol | status |
|---|---|---|
| MB1 task-length scaling | 1--10 turns, 10 paired manifests/point, fixed turn width 16 and closure 4 | complete; 100 paired cases |
| MB2 chronology | 9 fault positions, 10 paired manifests/position, 10 turns | complete; 90 paired cases |
| MB3 dependency sweep | 21 values, 10 paired manifests/value, 5 turns | complete; 210 paired cases |
| Boundary crossover | derived from the same 400 MB1--MB3 paired manifests | complete; no additional cases |
| Supporting sensitivity | turn count, fault position, shape, interleaving | complete; no missing paired cells |

### 2.8 Real-agent trajectory incidence and case-study plan

**Question and scope.** How often does a real agent trajectory contain useful
work that correct turn-, temporal-, or branch-level recovery would discard
after one of its actual mutating steps becomes faulty? This is an
observational *opportunity* study. It cannot estimate the natural frequency of
agent faults or the realized savings of a recovery system. The YoloFS
290-report study classifies public misuse reports; our unit is an observed
agent execution, not an incident report or a synthetic manifest.

**Existing evidence and gap.** The current native SWE-agent pilot contains 20
successful source/replay trajectories and 638 turns. Six turns in three tasks
pass its strict gate of at least two independent stateful components. Its
counterfactual token weights are serialized tool-call payload sizes, not LLM
usage. The official 541-task experiment seeds a synthetic pre-crash trajectory
and randomized fault; its rows measure recovery under injection, not the
prevalence of this structure in unmodified agent behavior. Neither dataset
alone supports a claim that the opportunity is common.

**Cohort and capture.** Run the real agent once per official task from a clean
workspace, without fault injection, rollback, a seeded reference solution, or
recovery hints. This clean execution is the native trajectory; retain its
original agent turns and every tool step, including read-only and failed
steps. Run four independent source-system/benchmark strata: Codex on SWE-Bench
Lite, Codex on Terminal-Bench, SWE-agent on SWE-Bench Lite, and SWE-agent on
Terminal-Bench. Target all available 300 SWE-Bench Lite and 241 Terminal-Bench
task IDs in each source-system stratum, without selecting by observed
advantage. Freeze the model (Luna max for both agents), harness, ordinary
task prompt, and task revision before analysis. Retain every captured attempt
with its task ID, attempt index, and configuration provenance, including
partial and failed runs; repeated attempts must not be silently deduplicated.
Use existing clean runs when their native step records survive; otherwise
rerun clean with passive trace capture. The current `clean_recovery` prompt describes a recovery-context
control, while `no_fault` uses an ordinary task prompt but lacks the complete
transaction step export; neither mode's aggregate CSV alone is a native
dependency trace. Record task success separately, never use it to filter
recorded steps, and report missing logs, replay
mismatches, environment failures, unsupported effects, and duplicate attempts
in a flow table. Public SWE-agent/Claude traces are a separate source-system
stratum, never merged silently with new Codex/Luna trajectories. Do not infer
native dependencies from the official experiment's synthetic recovery
documents. Test failure, incomplete execution, commit failure, and capture
warnings remain labels, not analysis inclusion criteria.

Terminal-Bench clean trajectories must run the agent inside each task's
official Docker image, preserving image paths such as `/data`; verifier files
must not be placed in the agent-visible workspace. The final workspace must
then pass the official image's `run-tests.sh` before success is recorded.
Missing verifier dependencies or failed downloads are environment errors, not
task failures. Earlier host-projected clean prechecks are excluded from this
cohort. The current formal Terminal-Bench runner still executes the agent on
the host and therefore cannot be labeled an image-native agent run merely
because its final verdict uses Docker.

**Opportunity census.** Capture each real step's observed reads, writes,
deletes, negative lookups, object identity, turn, timestamp, and tool-result
provenance. For every actual write/delete step with later observed work,
counterfactually mark it faulty. Compute its observed dependency closure and
the valid steps removed by each correct coarse boundary. A survivor counts as
retention opportunity only when its filesystem and explicit context
dependencies are accounted for; unresolved effects are reported as uncertain
and never presented as proven savings. Freeze the candidate-step rule and policy
semantics before inspecting outcomes. Keep all zero-opportunity tasks and all
eligible fault sites; do not select a maximally favorable root per task for
the primary statistic. Separately characterize long dependencies by the
number of steps and turns spanned by each observed edge, the longest directed
dependency path per task, and how far an eligible root's dependent work
extends. A long chain alone is not evidence of recovery waste: count the
independent, useful steps interleaved with it and compare those steps with
what each coarse boundary would discard.

**Reported denominators and metrics.** Report unique task IDs, captured
attempts, recorded turns and steps, mutating roots, and attribution warnings
separately for all four agent-by-benchmark strata. Do not pool
Codex and SWE-agent turn-level measurements: a Codex `exec` is one CLI turn
containing multiple native tool events, while SWE-agent exposes its own model
turns. Primary prevalence is
the fraction of recorded tasks with at least one positive site, accompanied by
the fraction of eligible sites with positive waste; label the first an
*existence* metric, not a fault probability. Equal-weight tasks for aggregate
means and bootstrap intervals by task so long trajectories do not dominate.
Report zero mass, median/P90/P99 of discarded valid steps and serialized
tool-result token weight, and a same-turn versus cross-turn breakdown. Compare
Turn checkpoint, Temporal step, Whole branch, and Ideal oracle on identical roots;
exclude Root only from efficiency claims when it leaves invalid descendants.
Also report the distribution of edge spans and longest paths in steps and
turns, the fraction of tasks containing cross-turn dependencies, and waste
conditional on dependency span and interleaved independent work. Tool-result
token weight is a work proxy, never model or replay token usage.

**Validation and case replay.** Audit a preregistered sample of positive,
zero, and unknown classifications against the original syscall/effect logs;
report disagreement and fail closed on ambiguous dependencies. Before model
continuation, select cases from fixed strata (high, median, zero opportunity;
same-turn and cross-turn; both benchmarks), not by realized savings. Inject
the same fault at the selected real step for each policy and run the official
verifier plus survivor-file hashes and agent-context checks. Record all
successes, failures, environment exclusions, model tokens, replay tokens, and
wall time. Compare replayed cases only after checking that the same native
trajectory and fault schedule were used. The ideal boundary is an oracle
lower bound; implemented AgentTX results belong in Evaluation, not this
Motivation incidence claim.

**Outputs and acceptance.** Produce a frozen cohort manifest, source-trace
hashes, per-step effect/edge records, per-root policy counterfactuals, a
task-level summary including zeros, an exclusion table, and reproducible
positive/negative case timelines. A main-text incidence plot must show the
denominator and zero-opportunity mass; a separate case timeline may explain
the mechanism. Do not claim broad prevalence until both real-workload strata
have adequate usable coverage and the dependency audit passes. If incidence
is low or one suite lacks usable traces, report that result and narrow the
paper claim rather than changing the cohort or fault-site rule.

**Implementation route.** Add passive step/effect export to the official
clean task path before its workspace and session are destroyed, retaining the
ordinary `no_fault` task prompt and checking that tracing does not alter the
agent-visible workflow. Persist the full tool-call sequence, turn boundaries,
ledger/effect provenance, and source configuration; validate `fault_injected`
and `precrash_progress_seeded` are false. Reuse
`replay_sweagent_native_traces.py` only to replay an unchanged source action
sequence and export effect provenance; collect Codex native traces through
its task harness rather than reconstructing them from the injected official-run
CSV. Generalize
`analyze_intra_turn_traces.py` from its six-turn eligibility gate to a census
that retains every usable task, turn, and candidate root, including zeros.
Extend `bench_real_trace_counterfactual.py` to write the full per-root policy
table and uncertainty labels without plotting implemented causal recovery as
Motivation evidence. Keep the old 20-trace pilot immutable. Put the new cohort,
raw audit, exclusions, figures, and case-replay outputs under a separately
versioned `experiments/results/motivation/real_trajectory_audit/` directory;
never overwrite the controlled 400-manifest results.

**Execution started 2026-10-06.** The first cohort uses native SWE-agent
with Luna max on the ordinary fault-free task instruction. It records each
action through the in-process adapter; it does not inject a crash, seed a
reference patch, or give a recovery hint. The two completed preflight cases
(`django__django-10924` and `analyze-access-logs`) both show cross-turn
workspace-file dependencies, but neither has an observed independently
mutating survivor; they remain zero-opportunity examples. A preregistered
12-task phase-one sample, two tasks per short/medium/long stratum in each
benchmark, is in `cohort_phase1.json` under the run directory. Both agents
use these same task IDs, forming 24 agent-task cells across four strata.
Image-native outputs are separated by agent and suite under
`experiments/results/motivation/real_trajectory_audit/`; earlier
`clean_native_luna_max_20261006` host-projected Terminal-Bench records are
not part of the official-image cohort.
The separate 20-trace public SWE-agent/Claude replay is only a method
calibration stratum, not pooled with Luna. The current analysis counts
observed workspace-path dependencies and excludes tooling/cache paths; it
does not certify all other work as independent or convert step counts into
model-token savings. Codex's ordinary external transaction is one process
step. Its separate passive native capture observes Codex JSON tool events,
attributes syscall reads within each event interval, and fingerprints
workspace writes at event boundaries. Traces with overlapping or unsupported
mutating tool events or unattributed final writes are excluded; overlapping
read-only events are conservatively overattributed. Codex `web_search` is a
real step but an opaque context barrier, so work across it is never certified
independent. Codex is not treated
as one transaction step, nor are artificial model-turn boundaries inferred.
Each of the four strata is validated in its official Docker task image before
cohort analysis. Earlier Terminal-Bench host-projected traces remain excluded.
As of 2026-10-06, the frozen six-task pilot has six recorded native trajectories
in each of the four agent-by-benchmark strata (24 in total). The SWE-agent
Terminal-Bench set includes a submitted case whose official tests failed and
a case that ended with the agent's exit format after recording real steps.
Codex's SWE-Bench Requests case also failed official tests after a valid
39-step capture. All three traces are retained in the step census. A failed
official test never excludes recorded steps; verifier/environment errors leave
the success label unknown. Overlapping mutating Codex tool events make step
attribution uncertain and are retained with a capture warning. The earlier SWE-Bench
pilot verifier omitted the official test patch, so its success labels remain
unknown even though its six step traces are recorded. All pilot denominators,
duplicate attempts, and warnings are recorded in the four `analysis_*_phase1`
directories under the real-trajectory audit results.
After the 24-cell pilot, four persistent full-cohort captures were launched
from the remaining 294 SWE-Bench Lite and 235 Terminal-Bench tasks per agent.
They run in `agenttx_native_{swe,codex}_{swe,tb}` tmux sessions and write
`full_{sweagent,codex}_{swe,tb}_20261006.log` beside their result directories.
These captures are in progress, not completed evaluation results. The
SWE-Bench capture admits an exact Astropy image-build pin of `setuptools` to
`68.0.0` in `pyproject.toml`; all other source-content differences still fail
the image/base check.

## 3. Hard Constraints

- Repository: `/home/bfq/agenttx`
- Python: `/home/bfq/miniconda3/envs/agenttx/bin/python3.11`
- Do not run or plot `agenttx_causal` in motivation. Its implemented-system
  results belong in the later evaluation, not the opportunity study.
- Every policy in one paired cell receives the same materialized trace, fault
  root, dependency graph, token weights, and seed.
- Do not remove unfavorable points, failed cells, or dense-dependency points.
- A single extreme sparse case may be retained as an appendix regression test,
  but it is not main-text evidence and is not a substitute for the sweep.
- Crab's turn-boundary policy is represented only by `turn_checkpoint_proxy`; do not describe its
  numbers as measurements of the Crab artifact.

## 4. Dependency Value

For a single-root trace, let:

- `r` be the faulty operation;
- `S = {s | s occurs after r}` be the temporal suffix after the root;
- `C` be the transitive causal closure of `r`, including `r`.

Define:

```text
dependency_value = |(C - {r}) intersect S| / |S|
```

`dependency_value` is therefore the fraction of post-fault operations that
really depend on the fault:

- `0.0`: no later operation depends on the fault;
- `1.0`: every later operation depends on the fault;
- intermediate values: dependent and independent work coexist.

This definition is independent of branch length and does not move the fault root
to make a requested closure size feasible. The generator first fixes the total
operation count and `fault_position`, then rounds
`dependency_value * post_fault_operations` to obtain the dependent count.
Both requested and realized values must be written to every result row.

The old `closure_ratio = |C| / |E|` remains a compatibility field only. It
mixes fault position with dependency density and must not be used as the main
x-axis.

## 5. Recovery Policies

See the [bilingual terminology reference](docs/terminology.md) for canonical
Chinese/English names, policy IDs, and the separate experiment namespaces.

| canonical ID | figure label | rollback boundary | expected role |
|---|---|---|---|
| `no_recovery` | No recovery | none | negative correctness control |
| `root_only` | Root only | fault producer only | dependency-propagation ablation |
| `turn_checkpoint_proxy` | Turn checkpoint | every complete turn containing invalid effects | favorable turn-boundary baseline |
| `temporal_step` | Temporal step | fault root through end of the branch | temporal-suffix baseline |
| `whole_branch` | Whole branch | complete uncommitted branch | coarsest baseline |
| `ideal_oracle` | Ideal oracle | exact invalid closure | analytical upper bound |

These identifiers and labels are defined once in
`experiments/motivation_strategies.py`. Motivation figures plot all six
policies. End-to-end model experiments use a separate
namespace (`clean_recovery`, `crash_no_rollback`, `temporal_checkpoint`,
`whole_branch_abort`, and `causal`) and must not silently rename those modes
to microbenchmark policies.

`no_recovery` and `root_only` may be cheap but incorrect. Token or work
savings are compared only between policies that remove the complete invalid
closure and pass all correctness checks.

## 6. Primary Sweep

The headline experiment is a one-factor sweep over dependency density:

| factor | primary value(s) |
|---|---|
| turn count | 5 |
| operations per turn | 16 |
| fault position | 25% |
| dependency value | 0.00 through 1.00 in increments of 0.05 (21 points) |
| graph shape | layered |
| interleaving | alternating |
| seeds/cases per gradient | 0 through 9 (10 cases) |

These 21 dependency points are preregistered. Report all points, including
points where Ideal oracle ties a coarse baseline.

Primary plots:

1. `dependency_value` versus useful valid operations wasted.
2. `dependency_value` versus valid-token retention.
3. `dependency_value` versus ideal savings relative to temporal and turn
   checkpoint rollback.

Use one consistent line-chart style. Each policy is one line; show means with
95% paired bootstrap confidence intervals and retain the raw points.

## 7. Sensitivity Sweeps

Change one factor at a time from the primary configuration:

| sweep | values |
|---|---|
| turn count (task length) | 1 through 10 |
| fault position | 0.10, 0.25, 0.50, 0.75, 0.90 |
| graph shape | chain, fan-out, layered |
| interleaving | independent-before, alternating, independent-after |

Every sensitivity sweep retains the same 21 dependency values from `0.00`
through `1.00` in increments of `0.05`. Use 10 seeds/cases per gradient. Do
not form an unnecessary full Cartesian product of
all nuisance factors; the primary sweep and one-factor sensitivity sweeps are
the intended design.

## 8. Metrics

For all operations `E`, invalid closure `C`, valid operations `V = E - C`, and
policy rollback set `R_p`, record:

```text
valid_retention        = 1 - |R_p intersect V| / |V|
invalid_removal        = |R_p intersect C| / |C|
useful_steps_wasted    = |R_p intersect V|
useful_tokens_wasted   = sum(token_weight(s), s in R_p intersect V)
invalid_steps_retained = |C - R_p|
```

Also record requested and actual dependency value, fault position, closure
size and legacy closure ratio, exact rollback set, manifest hash, rollback
time, and all four correctness views: merged overlay, conversation, committed
host, and verifier.

Tokenizer-attributed work is not billed model usage. Any later model replay
must report prompt, completion, and total usage separately.

## 9. Correctness Gates

Before accepting the analytical sweep:

1. For every requested dependency value, assert the realized dependent count
   equals `round(dependency_value * suffix_size)` and the reported actual value
   equals that count divided by the suffix size.
2. Assert the generator realizes the requested dependency value exactly after
   documented discrete rounding.
3. Run physical-overlay cells across all five registered turn-count groups and
   representative dependency values. The physical gate uses six distinct
   manifests per group because it validates execution semantics rather than
   estimating a population mean.
4. For every physically executed policy, check the merged overlay,
   conversation active calls, committed host state, and verifier result.
5. Assert policies in a paired cell share the same manifest hash.

The fixed 128-operation sparse trace remains an appendix regression test. It
must not appear as a standalone main-text motivation result.

## 10. Breakdown

For each non-ideal policy, decompose the gap to `ideal_oracle` into invalid
work retained and additional valid work discarded. This is a policy-granularity
breakdown, not an AgentTX component breakdown.

## 11. Microbenchmark Scope Boundary

AgentTX causal effectiveness, ledger/query overhead, compact-context cost,
commit latency, RSS, and model usage belong in the later system evaluation.
They must not be mixed into this motivation dataset.

## 12. Execution

Run focused tests first:

```bash
cd /home/bfq/agenttx
export PYTHONPATH=/home/bfq/agenttx/src:/home/bfq/agenttx
PY=/home/bfq/miniconda3/envs/agenttx/bin/python3.11

"$PY" -m pytest -q tests/test_intra_turn_recovery.py
```

Run the deterministic dependency sweep:

```bash
"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 \
  --dependency-values 0 0.05 0.10 0.15 0.20 0.25 0.30 0.35 0.40 0.45 \
    0.50 0.55 0.60 0.65 0.70 0.75 0.80 0.85 0.90 0.95 1.0 \
  --turn-counts 5 --widths 16 \
  --fault-positions 0.25 \
  --shapes layered \
  --interleavings alternating \
  --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark
```

Run MB1--MB2 with:

```bash
"$PY" experiments/scripts/bench_controlled_motivation.py \
  --output-dir experiments/results/motivation/controlled_case
```

Then run the one-factor sensitivity sweeps:

```bash
DEPENDENCY_VALUES=(
  0 0.05 0.10 0.15 0.20 0.25 0.30 0.35 0.40 0.45 0.50
  0.55 0.60 0.65 0.70 0.75 0.80 0.85 0.90 0.95 1.0
)

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 1 2 3 4 5 6 7 8 9 10 --widths 16 --fault-positions 0.25 \
  --shapes layered --interleavings alternating --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/turn_count

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 5 --widths 16 --fault-positions 0.10 0.25 0.50 0.75 0.90 \
  --shapes layered --interleavings alternating --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/fault_position

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 5 --widths 16 --fault-positions 0.25 \
  --shapes chain fan-out layered --interleavings alternating --repeats 10 \
  --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/shape

"$PY" experiments/scripts/bench_intra_turn_recovery.py \
  --phase m1 --dependency-values "${DEPENDENCY_VALUES[@]}" \
  --turn-counts 5 --widths 16 --fault-positions 0.25 --shapes layered \
  --interleavings independent-before alternating independent-after \
  --repeats 10 --m1-execution analytical \
  --output-dir experiments/results/motivation/microbenchmark/sensitivity/interleaving
```

Never merge rows from an incompatible schema or an older closure-ratio-only
run.

## 13. Required Artifacts

```text
experiments/results/motivation/microbenchmark/config.json
experiments/results/motivation/microbenchmark/motivation_recovery_raw.csv
experiments/results/motivation/microbenchmark/motivation_recovery_summary.csv
experiments/results/motivation/microbenchmark/REPORT.md
experiments/results/motivation/controlled_case/config.json
experiments/results/motivation/controlled_case/controlled_recovery_raw.csv
experiments/results/motivation/controlled_case/controlled_recovery_summary.csv
motivation/FIG-Motivation-Dependency.pdf
motivation/FIG-Motivation-Dependency.png
motivation/FIG-Motivation-Coarse-Waste.pdf
motivation/FIG-Motivation-Coarse-Waste.png
motivation/FIG-Motivation-Suite.pdf
motivation/FIG-Motivation-Suite.png
motivation/FIG-Motivation-Boundary-Crossover.pdf
motivation/FIG-Motivation-Boundary-Crossover.png
motivation/FIG-Motivation-Sensitivity.pdf
motivation/FIG-Motivation-Sensitivity.png
```

The report must state the complete sweep grid, planned and completed paired
cells, correctness-gate status, all exclusions with reasons, and the Git commit
or dirty-worktree fingerprint used for the run.

## 14. External Artifact Baselines

The paper's recovery comparison must include real external artifacts where they
are available. Table 1 classifies semantics; it does not imply that every row is
runnable. Keep author-reported numbers separate from measurements produced on
our host.

| system | native boundary | artifact status | experiment status |
|---|---|---|---|
| try | one opaque command effect set | MIT; pinned locally | run now |
| Sandlock | one sandboxed process run | Apache-2.0; builds locally, but host kernel has Landlock disabled | run later on a Landlock-enabled host as a process-boundary/overhead baseline |
| YoloFS | session snapshot | GPL-2.0; pinned and mounted locally | run now |
| BranchFS | leaf branch | MIT; pinned and mounted locally | run now |
| Waypoint | filesystem + process checkpoint | Apache-2.0; pinned; CRIU 4.2.1 passes in the privileged evaluation container | run now, but report container results separately from bare-host latency |
| Crab | sandbox checkpoint | no official artifact located | qualitative only; never label a proxy as Crab |
| DeltaBox | sandbox checkpoint | authors state artifact is not public | qualitative only |

The common artifact-native experiment reuses the microbenchmark's materialized
multi-turn manifests. Every system receives the same initial workspace, turn
boundaries, dependency value, fault location, and crash notification. It then
uses only its native recovery operation: command/process discard for try and
Sandlock, `snapshot`/`travel` for YoloFS, leaf `abort` for BranchFS, checkpoint
`fork` for Waypoint, and dependency-selected recovery for AgentTX. Do not graft
AgentTX's dependency scheduler onto an external artifact.

Validate every recovered workspace with one shared oracle. Report invalid-state
removal, valid-state retention, recovery latency, end-to-end latency, setup
cost, storage amplification, and privilege/kernel requirements. A baseline that
cannot represent a selective survivor state should use its native coarse
boundary and record the valid work it discards. Environment failures are
reported separately and excluded from performance aggregates.

The immediate matched run set on this host is AgentTX, try, YoloFS, BranchFS,
and Waypoint. Waypoint runs in a privileged, network-disabled container whose
runtime directories are bind-mounted from host ext4; do not combine its latency
with bare-host latency without an explicitly matched container run. Sandlock
joins on a Landlock-enabled kernel. The adapter and raw results must record
the upstream Git commit, local patches, command line, host fingerprint, and
whether each cell exercised the external artifact rather than a policy proxy.

Paper results must use the complete 400-manifest Motivation population: all
100 MB1 task-length cases, 90 MB2 fault-position cases, and 210 MB3 dependency
cases. Smoke tests and favorable subsets are validation only. The artifact
runner reconstructs these cases with `--suite motivation400` and refuses to run
if any `trace_id`/`manifest_hash` differs from the registered Motivation CSVs.
The full command is:

```bash
PYTHONPATH=src:. /home/bfq/miniconda3/envs/agenttx/bin/python3.11 \
  experiments/scripts/bench_external_recovery_artifacts.py \
  --suite motivation400 \
  --backends agenttx session_try yolofs branchfs waypoint \
  --workers 6 --resume \
  --output-dir experiments/results/external_recovery_motivation400_20260922
```

# Part II: System Evaluation

## 15. System Microbenchmark: End-to-End Execution of Motivation Cases

This full-system microbenchmark executes the exact cases used by the analytical
Motivation sweep. It is not a new workload and is not a substitute for the
official real-world suites. It exercises a model, tool runner, dependency
capture, recovery, context reconstruction, repair, and task verification in
one run while retaining the same controlled dependency structures.

### 15.1 Workload

Use the canonical 400 MB1--MB3 manifest IDs without changing their turn count,
operation order, graph shape, dependency value, interleaving, fault root, or
expected survivor set. Materialize each manifest as an executable repository
task with a generated task description and verifier. The generated task must
preserve the manifest's semantics exactly; it may not add dependencies merely
to make the result more favorable.

Every policy receives the same initial snapshot, task text, model
configuration, fault schedule, and token budget for a given case ID. A pilot
may execute a preregistered subset for protocol debugging, but subset results
do not appear in the paper and cannot replace the complete 400-case
Microbenchmark.

### 15.2 Policies and repetitions

Run `clean_recovery`, `crash_no_rollback`, `turn_checkpoint`,
`temporal_checkpoint`, `whole_branch_abort`, `causal`, and `ideal_oracle`.
`ideal_oracle` may receive the exact faulty step and closure; every other crash
policy receives only the same crash notification and policy-specific recovery
boundary. `causal` must localize the fault through AgentTX's own evidence.

Use three repeats per case and policy for the complete 400-case system
microbenchmark.
The primary model and reasoning configuration must match the real-world
workload. Prompt templates must be token-matched apart from unavoidable policy
payloads; report those payload tokens separately.

### 15.3 Metrics and acceptance

Report task success, invalid-effect removal, valid-effect retention,
workspace--context agreement, total model tokens including replay, tool calls,
recovery latency, and end-to-end wall time. Efficiency comparisons use paired
successful cells; all-attempt success and cost remain visible beside them.

The system microbenchmark is complete only when all 400 Motivation manifest IDs
have full-system results, every policy has the required three repeats, no
environment failure is silently excluded, and each paired cell has an
identical manifest and crash schedule.

## 16. Real-World Workloads: SWE-Bench Lite and Terminal-Bench

SWE-Bench Lite and Terminal-Bench are the paper's real-world workloads. Do not
describe them as a synthetic microbenchmark and do not add a separate toy
"real-world" scenario.

### 16.1 Scope

Run the complete available suites: 300 SWE-Bench Lite instances and 241
Terminal-Bench instances. The primary paper run uses one repeat per task and
policy; five repeated full-suite runs are not required. A stratified pilot may
be repeated to estimate variance, but it cannot replace the complete run.

The primary path uses a live agent and each suite's official verifier. Gold
patches and oracle solutions are permitted only for environment validation or
an explicitly labeled oracle upper bound; they are not task-success results.
Terminal-Bench also requires the agent to execute inside the task's official
Docker image; running only its verifier there is insufficient. The current
formal host-projected Terminal-Bench rows are diagnostic, not comparable
real-world success results. Do not resume or publish them as a native-image
cohort. A new formal run must use the image-native agent path for every mode.

Freeze the same model, reasoning effort, prompt and crash schedule, task set,
wall timeout, verifier, and count-limit policy for all strategies and both
agent harnesses. Do not inherit per-task SWE-agent step/model-call caps from
the synthetic workload scale when Codex has no corresponding cap. An omitted
`--max-turns` means no count cap; any explicit cap belongs in `run_config.json`
and requires a separately matched Codex protocol. Keep incomplete cells and
infrastructure failures visible in the denominator rather than silently
treating an expected-cell count as completed data.

### 16.2 Recovery protocol

Each crash-enabled task must inject the same registered failure schedule across
policies without disclosing the faulty step. The agent is told only that a
crash has occurred. Coarse policies apply their declared boundary; AgentTX may
write its inferred localization to the recovery handoff after searching its
ledger; only `ideal_oracle` may directly disclose the exact failed step.

Primary modes are `clean_recovery`, `crash_no_rollback`,
`temporal_checkpoint`, `whole_branch_abort`, and `causal` with compact context.
Add `turn_checkpoint` when the runner exposes a distinct multi-turn boundary.
All crash modes use the same fault count and positions. `clean_recovery` has no
injected crash and defines the unavoidable task cost, not the recovery saving.

### 16.3 Metrics

Report official success, crash-recovery success, workspace--context agreement,
total tokens (`main + replay`), wall time, recovery time, tool calls, and
environment failures. Present SWE-Bench and Terminal-Bench separately before
their aggregate. Token comparisons must include success rate and paired-success
sample count; a failed low-token run is not a saving.

## 17. System Breakdown

Run the Breakdown on the same 400 Motivation manifests so every component sees
the same complete trajectory. Compare:

| configuration | removed mechanism | expected observable failure/cost |
|---|---|---|
| Full AgentTX | none | reference |
| No READ/NEGATIVE capture | automatic producer--consumer discovery | invalid descendants survive |
| Path-only identity | alias/object tracking | alias-related corruption survives or recovery fails closed |
| Temporal history only | object-aware selective reconstruction | valid interleaved versions are lost |
| Workspace-only rollback | unified context closure and rewind | stale tool results disagree with files |
| One-shot worker | persistent execution | same semantics, higher per-step latency |
| Full context handoff | compact recovery handoff | same semantics, larger prompt/token cost |

The first four rows explain correctness and retention. The final two explain
engineering cost. Do not combine a semantically incorrect ablation with latency
averages as if it were a performance optimization. Report success, invalid
removal, valid retention, context agreement, tokens, and wall time for every
row.

## 18. Appendix Correctness and Interrupted-Recovery Tests

Correctness evidence belongs in the appendix and artifact, with a compact
summary in the main text.

### 18.1 Semantic matrix

Cover ordinary reads/writes/deletes, negative lookup followed by creation,
same-path multi-version updates, directory ancestry, rename, symlink, supported
hard-link groups, fd-relative operations, multiple fault roots, repeated
rollback, reload, and the fail-closed identity boundary. For each case assert:

1. every selected filesystem effect disappears;
2. every supported independent effect remains;
3. every invalid tool-result span disappears;
4. surviving messages and files refer to the same active step set;
5. a second identical recovery is a no-op.

### 18.2 Interrupted-recovery experiment

This experiment targets interruption during rollback, not publication to the
user workspace. Inject `SIGKILL` at the following recovery phases:

1. after the unified recovery plan is recorded but before reconstruction;
2. after a fresh upper generation is built but before it becomes active;
3. immediately after workspace generation swap but before context rewrite;
4. after context rewrite but before metadata persistence;
5. after persistence but before the recovery response reaches the agent.

After each interruption, reload the same session and resume recovery. The only
acceptable observable states are the complete pre-recovery pair or the complete
post-recovery pair. A workspace/context mixture, resurrected invalid message,
lost independent effect, or duplicated recovery action is a failure. Run at
least 20 injections per phase and publish every phase result. This test is a
required gate before claiming crash-safe synchronized rollback.

## 19. Other Overheads

Measure costs not captured by task success or total wall time:

1. dependency tracing overhead by command class and trace volume;
2. history storage amplification versus repository size, changed bytes, and
   trajectory length;
3. ledger and message-index memory growth versus tool-step count;
4. closure planning and reconstruction latency versus closure size, affected
   path count, alias-group size, and number of fault roots;
5. context reconstruction time and prompt tokens for full versus compact
   handoff;
6. startup, teardown, reload, and persistent-worker fallback costs.

Report mean, p50, p95, and p99 where repeated measurements exist. Include RSS,
bytes written, snapshot bytes, metadata bytes, and recovery latency rather than
reporting only normalized percentages. Unsupported cells and environment
failures remain visible.

## 20. Global Execution Rules

- Freeze code, prompts, model, reasoning effort, task versions, and verifiers
  before a canonical run.
- Run order is analytical microbenchmark, system-microbenchmark pilot,
  complete system microbenchmark, real-world environment check, full
  SWE-Bench/Terminal-Bench, Breakdown,
  appendix correctness/interruption matrix, then Other Overheads.
- Every result records the Git revision and dirty diff hash, environment,
  manifest hash, model configuration, prompt hash, usage source, and exclusion
  reason.
- Environment failures are excluded from task-success denominators only when
  reported separately and rerun under the identical frozen configuration.
- Never select only favorable cases or combine results from incompatible code,
  prompt, or result schemas.
