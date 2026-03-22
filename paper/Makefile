# paper/Makefile – build the IEEE paper with latexmk
# Usage:
#   make          – build PDF (default)
#   make clean    – remove auxiliary files
#   make distclean – also remove the PDF
#   make assets   – (re)generate tables/figures from results/
#   make all      – assets + PDF

MAIN   := main
LATEXMK := latexmk
LATEXMK_FLAGS := -pdf -interaction=nonstopmode -halt-on-error

ASSETS_SCRIPT := ../tools/paper/generate_paper_assets.py

.PHONY: all pdf assets clean distclean

all: assets pdf

pdf:
	$(LATEXMK) $(LATEXMK_FLAGS) $(MAIN).tex

assets:
	cd .. && python3 $(ASSETS_SCRIPT)

clean:
	$(LATEXMK) -c $(MAIN).tex
	rm -f $(MAIN).bbl $(MAIN).run.xml

distclean: clean
	rm -f $(MAIN).pdf
