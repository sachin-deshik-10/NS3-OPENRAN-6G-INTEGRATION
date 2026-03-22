# Research Paper Pipeline

This document explains the automated pipeline that turns NS-3 O-RAN 6G
simulation outputs into a compiled IEEE-style PDF paper.

---

## Overview

```
results/data/*.csv + *.json
        │
        ▼
tools/paper/generate_paper_assets.py
        │
        ├─► paper/tables/*.tex   (booktabs tables)
        ├─► paper/figures/*.tex  (TikZ/PGFPlots figures)
        └─► paper/sections/auto_results.tex
                │
                ▼
          paper/main.tex  (IEEEtran skeleton)
                │
                ▼  latexmk -pdf
          paper/main.pdf
```

---

## Prerequisites

| Tool | Install (Debian/Ubuntu) |
|------|------------------------|
| Python ≥ 3.8 | `sudo apt-get install python3` |
| TeX Live ≥ 2020 | `sudo apt-get install texlive-base texlive-latex-extra texlive-science` |
| `latexmk` | `sudo apt-get install latexmk` |
| `pandas` *(optional)* | `pip3 install pandas` |

---

## Step 1 – Run simulations to populate `results/`

```bash
# Full demonstration (writes results/data/*.csv and results/data/*.json)
python3 oran_6g_demonstration.py

# Or run any other simulation script; outputs must land under results/
```

The generator auto-discovers all `.csv` and `.json` files under `results/`
and extracts common KPI columns (throughput, latency, RSRP, SINR, CPU, etc.)
by fuzzy column-name matching.

---

## Step 2 – Generate LaTeX assets

```bash
python3 tools/paper/generate_paper_assets.py
```

This writes (or overwrites) the following files:

| File | Contents |
|------|---------|
| `paper/tables/sim_config.tex` | Simulation configuration table |
| `paper/tables/handover_kpi.tex` | Handover KPI comparison table |
| `paper/tables/mec_kpi.tex` | MEC service KPI table |
| `paper/tables/ml_summary.tex` | ML/FL training summary table |
| `paper/figures/latency_cdf.tex` | Latency CDF (PGFPlots) |
| `paper/figures/throughput_vs_load.tex` | Throughput vs load (PGFPlots) |
| `paper/figures/rsrp_timeseries.tex` | RSRP/SINR time series (PGFPlots) |
| `paper/sections/auto_results.tex` | Auto-generated results paragraph |

If `results/` is empty or no matching data is found, syntactically valid
placeholder `.tex` files with `TODO` markers are produced instead.

### Options

```
python3 tools/paper/generate_paper_assets.py --help

  --results PATH   Path to results directory (default: <repo>/results)
  --paper   PATH   Path to paper directory   (default: <repo>/paper)
```

---

## Step 3 – Edit narrative sections

The hand-written section files are **not** overwritten by the generator:

| File | Purpose |
|------|---------|
| `paper/sections/introduction.tex` | Introduction narrative |
| `paper/sections/related_work.tex` | Related work |
| `paper/sections/system_architecture.tex` | Architecture description |
| `paper/sections/algorithms.tex` | Algorithm descriptions + pseudocode |
| `paper/sections/experimental_setup.tex` | Experiment configuration narrative |
| `paper/sections/results.tex` | Results section (wraps auto-generated snippets) |
| `paper/sections/discussion.tex` | Discussion |
| `paper/sections/conclusion.tex` | Conclusion |
| `paper/references.bib` | BibTeX bibliography |
| `paper/figures/architecture.tex` | TikZ architecture diagram |

Fill in `% TODO` markers to complete the paper.

---

## Step 4 – Compile the PDF

```bash
# Option A: convenience script (runs generator + latexmk)
bash tools/paper/build_paper.sh

# Option B: Makefile inside paper/
cd paper && make          # runs generator + latexmk
cd paper && make pdf      # latexmk only
cd paper && make assets   # generator only

# Option C: direct latexmk
cd paper && latexmk -pdf main.tex
```

The compiled PDF is written to `paper/main.pdf`.

---

## CI Integration

A GitHub Actions workflow at `.github/workflows/paper.yml` runs the
asset generator on every push and pull request.
LaTeX compilation is attempted if `texlive-base` is available in the
runner environment.

To disable LaTeX compilation in CI (e.g., to keep CI fast), the workflow
can be configured to run with `--no-latex`:
```bash
bash tools/paper/build_paper.sh --no-latex
```

---

## Troubleshooting

**"latexmk not found"**
```bash
sudo apt-get install latexmk texlive-base texlive-latex-extra texlive-science
```

**"Package not found" LaTeX error**
```bash
sudo tlmgr install <package-name>
# or install the full TeX Live:
sudo apt-get install texlive-full
```

**Generator finds no data / placeholder tables**
- Ensure `results/data/` contains at least one `.csv` or `.json` file.
- Check that the CSV headers include KPI-like column names (see the column
  matching rules in `tools/paper/generate_paper_assets.py`).

**Figures look wrong after re-running simulations**
Simply re-run the generator and recompile:
```bash
python3 tools/paper/generate_paper_assets.py
cd paper && latexmk -pdf main.tex
```
