# L3 Consistency Report: AgentTX | OSDI | 2026-08-09

## Scope

This audit checks `paper/main.tex` against `stream-L0.md`, `stream-L1.md`, the
systems-paper writing guide, and the committed AgentTX evidence documents.  It covers
the full manuscript from the abstract through the conclusion.  It does not treat missing
future experiments as prose defects when the draft marks their boundary explicitly.

## Structural Checks 1--6

### Check 1: Thesis Trace -- PASS

Every major section traces to the L0 thesis that causal effects, rather than temporal
position, should define the recovery unit.  Section 2 defines the mismatch, Section 3
measures it, Section 4 defines AET, Section 5 realizes it, and Section 6 tests semantic,
runtime, agent, and robustness outcomes.  eBPF appears only as an unmeasured discussion
alternative.

### Check 2: Design Coverage -- PASS for draft

- DP1 effect capture and ledger appears in Sections 4.2 and 5.1 and is isolated by the
  no-dependency experiment in RQ2.
- DP2 shared speculation and selective reconstruction appears in Sections 4.3 and
  5.2--5.3 and is tested by retention, runtime, crash, and reload experiments.
- DP3 durable publication appears in Sections 4.4 and 5.4.  RQ4 reports interrupted host
  materialization and policy persistence checks.
- DP4 performance engineering is explicitly labeled as additional optimization rather
  than a fourth correctness challenge.

The final submission should give WAL crash phases a compact table or pass count; the draft
does not claim external multi-path atomic visibility.

### Check 3: Challenge--Response and Causal Test -- PASS

- Dependency Discovery maps to observed effects, the ledger, and the dependency ablation.
- Object Identity maps to hierarchy and supported symlink normalization.  The lower
  hard-link probe is presented as a measured boundary, not solved coverage.
- Selective Reconstruction maps to versioned state, causal closure, overlap rejection,
  and the retention experiment.

All three challenges follow from the same root mismatch: state is indexed by command,
time, and pathname, while failures propagate through causal dependencies among versioned
effects.  Worker reuse and snapshot acceleration are not forced into this list.

### Check 4: Result Consistency -- PASS

The abstract, introduction, captions, evaluation, and conclusion agree on the following:

- 144 controlled real-overlay recovery runs;
- 100% independent-work retention and 100% invalid-descendant removal in that suite;
- 41% temporal retention at 64 calls and 4% invalid removal without dependencies;
- 1,335.7 and 2,891.0 replay tokens at the largest controlled input;
- 148.5 ms/step for full AgentTX, 43% below per-call isolation and 2.99x bare.

The manuscript consistently calls token results **avoided replay tokens**.  Temporal and
whole-session comparisons are **recovery-granularity emulations**, not external-system
measurements.  The real model selects and requests recovery; the benchmark performs the
controlled commit after validation.

### Check 5: Section Contracts -- PASS

Every section follows its L1 flow chain.  The Introduction uses the seven-step sequence:
context, approach taxonomy, measured challenges, root cause, key idea, techniques, and
evidence/contributions.  Evaluation uses RQ1--RQ4 with an opening hypothesis, evidence,
and closing result.  Robustness and hard-link identity limits are reported with their
disjoint-workspace and explicit-API scope.

### Check 6: Contribution Alignment -- PASS

The four contribution bullets cover problem evidence, AET, the AgentTX prototype, and
evaluation.  They do not claim causal rollback is the default, complete syscall/alias
coverage, total token savings, atomic external visibility, or superiority over external
artifacts.

## Prose Quality Check 7 -- PASS

The manuscript was read linearly and then scanned again for length and symbol hygiene.

- **7a sentence length:** all prose sentences are at or below 30 words after excluding
  equations, tables, and separate list items.
- **7b paragraph length:** all prose paragraphs contain at most six sentences; the abstract
  contains six logical sentences.
- **7c conciseness:** removed filler and ambiguous baseline lists; no banned AI-style terms
  from the writing guide remain.
- **7d paragraph structure:** motivation, model, implementation, and each RQ begin with a
  point or hypothesis before mechanism and evidence.
- **7e flow:** root cause follows symptoms; AET precedes AgentTX implementation; semantic
  evidence precedes limitations and related work.
- **7f vocabulary:** `shared snapshot`, `avoided replay tokens`, `crash recoverability`, and
  `recovery-granularity emulation` have distinct meanings.
- **7g pronouns:** recovery actors and commit ownership are explicit.
- **7h symbol hygiene:** no causal `---`, prose arrows, or internal DP-to-challenge notation.
- **7i lists:** standalone lists use `itemize`; contribution bullets remain one line each in
  source.
- **7j causal clarity:** core correctness mechanisms are linked to challenges; worker and
  incremental snapshot optimizations are labeled separately.

## LaTeX and Visual QA

- `latexmk` completes with no undefined references or citations.
- Output is US Letter, nine PDF pages including references, with the main paper ending on
  page 8.
- Fonts are embedded and all pages were rendered to PNG for visual inspection.
- No text, table, equation, caption, or footer is clipped.
- The robustness figure was changed to a single-column auxiliary figure so it remains near
  RQ4 instead of appearing alone after the references.
- Remaining underfull-box warnings are cosmetic line-breaking effects.  There are no
  overfull text boxes.

## Evidence Still Needed for Submission

These are experimental gaps, not inconsistencies hidden by prose:

1. VM CPU/vCPU, memory, storage, and frequency-control details.
2. Interleaved runtime repetitions with variance or confidence intervals.
3. Artifact-native external baseline runs or a documented blocker table.
4. Multi-package repositories, additional models, and shared-path concurrency.
5. A compact WAL crash-phase and syscall-coverage table.

**Final verdict:** the draft is structurally consistent and compilable.  Its central
semantic claim is supported by current controlled evidence, while the text keeps external
comparison, object identity, tracing completeness, and token scope explicit.
