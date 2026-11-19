# L3 Consistency Report: AgentTX | OSDI | 2026-09-23

## Scoped try-boundary clarification — 2026-10-29

The user-approved paper-writing edit now defines try's wrapped invocation as
a command, script, or whole agent session. Table 1, Introduction, Motivation,
Implementation, and Related Work consistently distinguish isolation lifetime
from internal step attribution and dependency-selected recovery. Section 5
connects native tool boundaries to file/history/message records and matching
visible-context reconstruction, while preserving the black-box outer-step
and manifest-handoff limits. The Section 5 draft is synchronized.

Scoped L3 checks pass; all numeric tokens, citations, labels, and figure paths
are preserved. Evaluation and Discussion are unchanged. Tectonic rebuilds a
13-page PDF; Figure 7 remains single-column on page 7. Modified content on
pages 3, 8, 9, and 11 was visually checked. See
`review/try-boundary-clarification-20261029.md` for the scoped audit.
Previously recorded whole-paper issues remain open.

## Scoped figure integration — 2026-10-29

The accepted design figure is integrated into Section 5 as Figure 7, with
workflow prose explaining shared try execution, layer transitions, the joint
dependency set, historical targets, isolated checking, and live restoration.
The user's subsequent white-background correction is included in every asset.
The paper now builds with Tectonic 0.17.0 and, following the user's latest layout
request, the figure appears in a single column on page 7 of the current 13-page
PDF. The final log has no undefined references, citations,
fonts, overfull boxes, or PDF-version warnings; underfull spacing warnings remain.
See `review/design-figure-integration-20261029.md` for scoped findings and checks.
This supersedes the earlier compiler-unavailable status for the current PDF.
Whole-paper L3 remains open for the previously recorded issues outside this scope.

## Scoped design revision — 2026-10-27

The approved English revision has replaced Sections 4–5 and aligned the two
challenge passages. The scoped design audit passes: candidate localization,
joint dependency closure, isolated checking, historical restoration, and visible
context reconstruction follow one causal workflow without assuming a known
true root. See `review/design-english-consistency-20261027.md` for the current
findings and unresolved issues outside this revision.

Static label, citation, environment, prose-length, and diff checks pass.
Evaluation and subsequent sections are unchanged from the pre-edit manuscript.
The current environment lacks a TeX compiler; portable compiler downloads failed,
so this revision has not rebuilt `paper/main.pdf`. Earlier compilation claims
below describe the earlier audit, not validation of the revised manuscript.
Whole-paper L3 remains OPEN.

## Current status

**OPEN -- evaluation restructuring required.** The core rollback thesis and
workspace--context semantics remain consistent, but the canonical experiment
order changed after the last paper audit. The required order is now:

```text
Microbenchmark
  -> Real-world Workloads (SWE-Bench Lite and Terminal-Bench)
  -> Breakdown
  -> Other Overheads
```

Correctness and interrupted-recovery tests move to the appendix. The current
`paper/main.tex` still presents the previously completed pilot evidence as four
RQ subsections. It must not be marked structurally complete until the same 400
Motivation manifests have complete full-system Microbenchmark results and the
complete live-agent real-world runs replace the pilot/oracle cells. The
Evaluation prose must then be reordered accordingly.

## Scope

This audit checks `paper/main.tex` against the L0 thesis, L1 section contracts,
the current result artifacts, and the systems-paper writing guide. It covers
novelty hierarchy, terminology, quantitative claims, prose, references, and
the compiled document.

## Novelty and structure

**PASS.** The paper now states one claim throughout: the trajectory is the
speculation unit, while a dependency-defined subgraph of versioned effects is
the recovery unit. AET is the abstraction that separates these units. AgentTX
is the Linux realization through effect capture, object-aware history,
selective workspace reconstruction, and context-aligned rollback.

The three challenges map directly to the abstraction and implementation:

- Dependency Discovery maps to typed effect capture and ledger edges.
- Object Identity maps to hierarchy, symlink, rename, and tested alias handling.
- Selective Reconstruction maps to affected-subgraph selection, historical versions,
  and conversation rewind.
OverlayFS, tracing, persistent workers, and incremental snapshots are presented
as mechanisms or optimizations, not as independent conceptual contributions.
Publication is explicitly outside the paper's recovery scope.

## Results and claim boundaries

**PASS.** The motivation reports one population of 400 paired manifests; no
separate small-case validation is part of the paper protocol. The current
results report 54.02% and 56.90% collateral
loss for turn-checkpoint and temporal-step recovery, and 97.5% non-prefix exact
survivor states. The evaluation consistently reports 100% independent-work
retention, 100% invalid-descendant removal, and replay avoidance of 1,424.7 and
3,340.3 tokens at 48 entries.

The paper distinguishes controlled recovery semantics from official task
repair, local boundary emulations from artifact-native systems, and avoided
replay from total session-token savings. Filesystem identity, external side
effects, publication scope, model count, and native-agent evidence remain
explicitly bounded.

The 400-case artifact comparison is complete for try, BranchFS, YoloFS, and
Waypoint. YoloFS and Waypoint each discard 65.89% of valid work, compared with
54.02% for the oracle-aided turn-boundary upper bound. BranchFS and try discard
the complete speculative unit through their native abort/discard operations.

Figure 3 also reports all four artifact-native results over the 210-case
dependency sweep. YoloFS and Waypoint select identical survivor states, as do
BranchFS and try. The figure names every system and distinguishes coincident
measurements with separate markers.

## Prose and terminology

**PASS.** Abstract, Introduction, AET, implementation, evaluation, related
work, and conclusion use the same abstraction--implementation hierarchy.
The Introduction summarizes the Motivation's three qualitative findings but
does not repeat its case counts, per-policy percentages, or figure-level data.
Motivation introduces the Ideal oracle as removing the failed step and its
dependent steps without assuming the later AET terminology. Paragraphs use at
most six sentences; substantive prose sentences use at most 30 words. Internal
experiment labels and stale three-challenge wording are absent.

## LaTeX and visual QA

- `make -C paper` completes and produces a 15-page PDF.
- No undefined references, undefined citations, overfull boxes, LaTeX errors,
  or duplicate figure/table anchors remain.
- All Motivation figures are single-column; their internal subplots render
  without clipping, overlap, or displacement.
- The selected motivation and external-artifact suites pass all 12 tests with
  the repository root on `PYTHONPATH`.

**Verdict:** the draft remains compilable and preserves the central novelty
claim, but L3 is open because Evaluation has not yet been migrated to the new
evidence order and still lacks the canonical system-microbenchmark,
full real-world, interrupted-recovery, and resource-overhead datasets.

## Scoped real-workload integration — 2026-10-30

Completed SWE and TB recovery campaigns are now integrated in RQ2, replacing
the earlier missing-real-workload status for this intervention-based study.
The final TB audit covers 2,892 terminal cells and 3,736 attempts. Cost pairs
retain failed/retry spend and exclude unknown or incompatible histories;
task outcomes, context/localization differences and protocol-validation scope
are stated separately. Unfavorable cost and success results remain visible.

Figure 8 is replaced by a compact success/savings grid; Figure 9 shows per-task
ratios. Both match the earlier SWE figure style and appear with RQ2 on pages
10–11. The 14-page PDF compiles without undefined references/citations,
overfull boxes or TeX errors; existing underfull warnings remain.

The scoped seven-check review, independent data/style reviews, source hashes,
and numeric/graphics validation are in
`review/realworld-results-20261030/L3-scoped-review.md` and adjacent artifacts.
The dedicated RQ2 draft is synchronized. Other missing evaluation sections,
formal uniform-budget evidence and the submission page budget remain open;
this is not whole-paper L3 completion.
