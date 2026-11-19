# Lancet paper

The manuscript is synchronized with the standalone OSDI-2027 paper repository:
https://github.com/Bigsmart408/OSDI-2027

Build from the project root:

```bash
make -C paper
```

The Makefile uses `latexmk`/`pdflatex` or `tectonic`; the output is `paper/main.pdf`.
`main.tex` contains the shared formatting, title, and chapter inputs.
Edit the abstract and chapters in `sections/`: `abstract.tex`, `introduction.tex`,
`background.tex`, `motivation.tex`, `design.tex`, `evaluation.tex`,
`discussion.tex`, `related-work.tex`, and `conclusion.tex`.

The bibliography is `references.bib`. Local table and figure inputs are in
`tables/`, `img/`, and `figures/design-workflow/`. Editable design diagrams and
their generators are in `figures/design-workflow/`.

Figure PDFs embed their fonts. Regenerating statistical and design figures
requires Times New Roman fonts under `~/.local/share/fonts/TimesNewRoman/`.
