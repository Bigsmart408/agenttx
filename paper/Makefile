LATEXMK ?= latexmk
TECTONIC ?= tectonic

.PHONY: all clean

all: main.pdf

main.pdf: main.tex references.bib usenix-2020-09.sty $(wildcard img/*.pdf)
	@if command -v "$(LATEXMK)" >/dev/null 2>&1; then \
		"$(LATEXMK)" -pdf -interaction=nonstopmode -halt-on-error main.tex; \
	elif command -v "$(TECTONIC)" >/dev/null 2>&1; then \
		"$(TECTONIC)" main.tex; \
	else \
		echo "error: install latexmk/pdflatex or tectonic before building" >&2; \
		exit 127; \
	fi

clean:
	@if command -v "$(LATEXMK)" >/dev/null 2>&1; then \
		"$(LATEXMK)" -C main.tex; \
	else \
		rm -f main.pdf main.aux main.bbl main.blg main.fdb_latexmk main.fls main.log main.out main.toc; \
	fi
