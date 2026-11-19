# AgentTX paper draft

This directory contains a compilable, anonymous USENIX/OSDI-style first draft.
The prose follows the argument structure documented in `REFERENCE_STRUCTURE.md`
while using only AgentTX-specific design and measurements.

Build on the evaluation VM:

```bash
cd /home/pengpeng/agenttx/paper
make
```

The build is self-contained: `main.tex`, `references.bib`,
`usenix-2020-09.sty`, the figures under `img/`, and the editable design figure
under `figures/design-workflow/` are all local.
The design figure is included directly from its vector PDF; regenerate that
file from the adjacent Python sources when updating the PPT.
The checked-in `main.pdf` is a convenience preview; the source of
truth is `main.tex` plus its bibliography and local figure assets.

Statistical figures use Times New Roman, with principal labels at 8 pt
against the 10 pt body text. To regenerate the figures, install the Microsoft
Times New Roman regular, bold, italic, and bold italic fonts. The plotting
setup registers `.ttf` files under `~/.local/share/fonts/TimesNewRoman/` and
requires Times New Roman rather than silently substituting another family.
The font is embedded in the generated PDFs; building the paper from those
PDFs does not require a local font installation.

The Makefile prefers `latexmk`/`pdflatex` and falls back to `tectonic`.  On the
evaluation VM, the latter is installed in the `agenttx` conda environment:

```bash
cd /home/pengpeng/agenttx/paper
conda run -n agenttx make
```

For a system-wide build, install `latexmk`/`pdflatex` or another
USENIX-compatible LaTeX distribution and run `make` directly.

Before submission, replace the anonymous author block, reconcile the venue's
current page/blindness rules, add externally runnable baselines, and audit every
claim against the latest result artifacts.
