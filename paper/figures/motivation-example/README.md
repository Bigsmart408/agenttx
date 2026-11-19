# Motivation rollback example (Figure 1)

Run `draw.py` with the paper figure environment to regenerate the editable
`AgentTX-Motivation-Rollback-Example.pptx`, PDF, SVG, and PNG previews:

```bash
/tmp/agenttx-paper-check-20261027/bin/python paper/figures/motivation-example/draw.py
```

The figure uses the shared primitives in `../design-workflow/` so that its
Times New Roman typography, white background, semantic red/green nodes, and
large triangular arrowheads match the overview and implementation figures.
Red calls carry the error; green calls remain valid, but the whole-execution
rollback boundary discards them too. The example still contains the same
six calls across three turns, with one dependency from `write A` to `read A`.

PDF and PNG are copied into `paper/img/FIG-Motivation-Rollback-Example.*`.
The `execution-vs-dependency-draft.*` preview filenames remain available.
The layout report records the calls, dependency, rollback set, text fit,
and canvas bounds.
