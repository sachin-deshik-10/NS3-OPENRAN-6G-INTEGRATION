#!/usr/bin/env bash
# tools/paper/build_paper.sh – convenience script to generate assets and compile the PDF
# Usage: bash tools/paper/build_paper.sh [--no-latex]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PAPER_DIR="${REPO_ROOT}/paper"

# ── Step 1: generate LaTeX assets ────────────────────────────────────────────
echo "==> Generating paper assets from results/ ..."
python3 "${SCRIPT_DIR}/generate_paper_assets.py" --results "${REPO_ROOT}/results" \
                                                   --paper   "${PAPER_DIR}"

# ── Step 2: compile PDF ───────────────────────────────────────────────────────
if [[ "${1:-}" == "--no-latex" ]]; then
  echo "==> Skipping LaTeX compilation (--no-latex)."
  exit 0
fi

if ! command -v latexmk &>/dev/null; then
  echo "WARNING: latexmk not found.  Install TeX Live and retry."
  echo "  On Debian/Ubuntu: sudo apt-get install texlive-base texlive-latex-extra texlive-science latexmk"
  exit 1
fi

echo "==> Compiling paper/main.tex ..."
cd "${PAPER_DIR}"
latexmk -pdf -interaction=nonstopmode -halt-on-error main.tex

echo ""
echo "==> Success!  PDF written to: ${PAPER_DIR}/main.pdf"
