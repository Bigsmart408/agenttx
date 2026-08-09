# AgentTX paper draft

This directory contains a compilable, anonymous USENIX/OSDI-style first draft.
The prose follows the argument structure documented in `REFERENCE_STRUCTURE.md`
while using only AgentTX-specific design and measurements.

Build on the evaluation VM:

```bash
cd /home/bfq/agenttx/paper
make
```

The build reads paper figures directly from `../motivation/`.  The checked-in
`main.pdf` is a convenience preview; `main.tex` and `references.bib` are the
source of truth.

Before submission, replace the anonymous author block, reconcile the venue's
current page/blindness rules, add externally runnable baselines, and audit every
claim against the latest result artifacts.
