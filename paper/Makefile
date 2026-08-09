LATEXMK ?= latexmk

.PHONY: all clean

all: main.pdf

main.pdf: main.tex references.bib usenix-2020-09.sty
	$(LATEXMK) -pdf -interaction=nonstopmode -halt-on-error main.tex

clean:
	$(LATEXMK) -C main.tex
