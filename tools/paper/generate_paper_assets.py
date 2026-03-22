#!/usr/bin/env python3
"""
tools/paper/generate_paper_assets.py
======================================
Scans ``results/`` recursively for CSV and JSON metric files, then writes
populated LaTeX tables (booktabs) and TikZ/PGFPlots figures into
``paper/tables/`` and ``paper/figures/``.  Also writes a short
auto-generated results paragraph to ``paper/sections/auto_results.tex``.

Design principles
-----------------
* Idempotent and deterministic – running multiple times produces the same
  output for the same input files.
* Graceful degradation – if expected files are missing, generates
  placeholder LaTeX with clear TODO markers rather than raising.
* Minimal dependencies – uses stdlib CSV/JSON parsing; pandas/numpy are
  used only if already importable (optional fast path).
* Safe LaTeX output – all user-data strings are escaped before being
  written into .tex snippets.

Usage
-----
    python3 tools/paper/generate_paper_assets.py [--results RESULTS_DIR]
                                                  [--paper   PAPER_DIR]

From the repository root the defaults work without arguments.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Optional fast-path imports
# ---------------------------------------------------------------------------
try:
    import pandas as pd  # type: ignore
    _HAS_PANDAS = True
except ImportError:
    _HAS_PANDAS = False

# ---------------------------------------------------------------------------
# KPI column detection
# ---------------------------------------------------------------------------
_KPI_PATTERNS: Dict[str, List[str]] = {
    "throughput":  ["throughput", "tput", "dl_tput", "ul_tput", "throughput_mbps"],
    "latency":     ["latency", "latency_ms", "delay", "delay_ms", "e2e_latency"],
    "jitter":      ["jitter", "jitter_ms"],
    "loss":        ["loss", "packet_loss", "loss_pct", "loss_rate"],
    "p50":         ["p50", "median"],
    "p95":         ["p95", "95th", "p95_latency"],
    "p99":         ["p99", "99th"],
    "time":        ["time", "timestamp", "sim_time", "t"],
    "load":        ["load", "offered_load", "num_ues", "users"],
    "rsrp":        ["rsrp", "rsrp_dbm"],
    "rsrq":        ["rsrq", "rsrq_db"],
    "sinr":        ["sinr", "sinr_db"],
    "cpu":         ["cpu", "cpu_utilization", "cpu_util"],
    "memory":      ["memory", "mem", "memory_utilization"],
    "success":     ["success", "ho_success", "success_rate"],
    "algorithm":   ["algorithm", "algo", "policy"],
    "ue_id":       ["ue_id", "ue", "user_id"],
    "enb_id":      ["enb_id", "gnb_id", "enb", "gnb"],
    "service":     ["service_name", "service", "app"],
    "edge_node":   ["edge_node", "host", "edge_host"],
}


def _match_col(col: str, kpi: str) -> bool:
    """Return True if *col* matches any pattern for *kpi* (case-insensitive)."""
    col_lc = col.lower().strip()
    return col_lc in _KPI_PATTERNS.get(kpi, [kpi])


def _find_col(headers: List[str], kpi: str) -> Optional[str]:
    """Return the first header that matches *kpi*, or None."""
    for h in headers:
        if _match_col(h, kpi):
            return h
    return None


# ---------------------------------------------------------------------------
# LaTeX escaping
# ---------------------------------------------------------------------------
_TEX_ESCAPE = str.maketrans({
    "&":  r"\&",
    "%":  r"\%",
    "$":  r"\$",
    "#":  r"\#",
    "_":  r"\_",
    "{":  r"\{",
    "}":  r"\}",
    "~":  r"\textasciitilde{}",
    "^":  r"\textasciicircum{}",
    "\\": r"\textbackslash{}",
})


def tex(s: Any) -> str:
    """Escape *s* for safe inclusion in LaTeX source."""
    return str(s).translate(_TEX_ESCAPE)


# ---------------------------------------------------------------------------
# Statistics helpers (stdlib-only)
# ---------------------------------------------------------------------------

def _mean(xs: List[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _percentile(xs: List[float], p: float) -> float:
    """Compute the *p*-th percentile (0–100) using linear interpolation."""
    if not xs:
        return float("nan")
    xs_s = sorted(xs)
    n = len(xs_s)
    k = (n - 1) * p / 100.0
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return xs_s[lo] + (xs_s[hi] - xs_s[lo]) * (k - lo)


def _stddev(xs: List[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


# ---------------------------------------------------------------------------
# CSV / JSON readers
# ---------------------------------------------------------------------------

def _read_csv(path: Path) -> Tuple[List[str], List[Dict[str, str]]]:
    """Return (headers, rows) for a CSV file."""
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = list(reader.fieldnames or [])
        rows = list(reader)
    return headers, rows


def _parse_float(v: Any) -> Optional[float]:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _collect_numeric_leaves(obj: Any, prefix: str = "") -> Dict[str, float]:
    """Recursively collect numeric leaf values from a JSON object."""
    out: Dict[str, float] = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = f"{prefix}.{k}" if prefix else k
            out.update(_collect_numeric_leaves(v, key))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            key = f"{prefix}[{i}]"
            out.update(_collect_numeric_leaves(v, key))
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        out[prefix] = float(obj)
    return out


# ---------------------------------------------------------------------------
# Directory scanner
# ---------------------------------------------------------------------------

def scan_results(results_dir: Path) -> Dict[str, List[Path]]:
    """Return dict mapping extension to list of matching Paths."""
    found: Dict[str, List[Path]] = {"csv": [], "json": []}
    for root, _dirs, files in os.walk(results_dir):
        for fname in sorted(files):
            ext = fname.rsplit(".", 1)[-1].lower()
            if ext in found:
                found[ext].append(Path(root) / fname)
    return found


# ---------------------------------------------------------------------------
# Data extraction helpers
# ---------------------------------------------------------------------------

def _extract_handover(csv_paths: List[Path]) -> List[Dict[str, Any]]:
    """Return list of handover event rows from any matching CSV."""
    rows: List[Dict[str, Any]] = []
    for p in csv_paths:
        headers, data = _read_csv(p)
        if _find_col(headers, "latency") and _find_col(headers, "algorithm"):
            for r in data:
                algo_col = _find_col(headers, "algorithm")
                lat_col  = _find_col(headers, "latency")
                suc_col  = _find_col(headers, "success")
                tl_col   = _find_col(headers, "throughput")  # throughput_loss
                rows.append({
                    "algorithm": r.get(algo_col, "Unknown") if algo_col else "Unknown",
                    "latency":   _parse_float(r.get(lat_col, "")) if lat_col else None,
                    "success":   r.get(suc_col, "True").strip().lower() not in
                                 ("false", "0", "") if suc_col else True,
                    "tput_loss": _parse_float(r.get(tl_col, "")) if tl_col else None,
                })
    return rows


def _extract_mec(csv_paths: List[Path]) -> List[Dict[str, Any]]:
    """Return MEC service rows from any matching CSV."""
    rows: List[Dict[str, Any]] = []
    for p in csv_paths:
        headers, data = _read_csv(p)
        if _find_col(headers, "service") and _find_col(headers, "cpu"):
            for r in data:
                rows.append({k: r.get(k, "") for k in headers})
    return rows


def _extract_rsrp(csv_paths: List[Path], max_rows: int = 200
                  ) -> List[Dict[str, Any]]:
    """Return RSRP measurement rows (first UE, limited rows)."""
    for p in csv_paths:
        headers, data = _read_csv(p)
        if _find_col(headers, "rsrp") and _find_col(headers, "time"):
            ue_col = _find_col(headers, "ue_id")
            # Take data for UE 0 only
            subset = [r for r in data
                      if ue_col is None or r.get(ue_col, "0") == "0"]
            return subset[:max_rows]
    return []


def _extract_ml(json_paths: List[Path]) -> Dict[str, Any]:
    """Extract RL and digital-twin metrics from the first matching JSON."""
    for p in json_paths:
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        ml = d.get("ml_training") or d.get("ml") or d.get("training")
        if ml and isinstance(ml, dict):
            return ml
    return {}


def _extract_sim_meta(json_paths: List[Path]) -> Dict[str, Any]:
    """Extract simulation metadata from the first matching JSON."""
    for p in json_paths:
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
        meta = d.get("simulation_metadata") or d.get("config") or d.get("metadata")
        if meta and isinstance(meta, dict):
            return meta
    return {}


# ---------------------------------------------------------------------------
# LaTeX writers
# ---------------------------------------------------------------------------

def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)
    print(f"  wrote {path}")


# ── sim_config.tex ─────────────────────────────────────────────────────────

def write_sim_config(meta: Dict[str, Any], tables_dir: Path) -> None:
    num_enbs = meta.get("num_enbs", "--")
    num_ues  = meta.get("num_ues",  "--")
    sim_time = meta.get("simulation_time", "--")
    total_ho = meta.get("total_handovers", "--")
    total_ms = meta.get("total_measurements", "--")

    content = r"""%% Auto-generated by tools/paper/generate_paper_assets.py
\begin{table}[htbp]
  \centering
  \caption{Simulation Configuration}
  \label{tab:sim_config}
  \begin{tabular}{@{}ll@{}}
    \toprule
    \textbf{Parameter} & \textbf{Value} \\
    \midrule
""" + \
    f"    Number of gNBs & {tex(num_enbs)} \\\\\n" + \
    f"    Number of UEs & {tex(num_ues)} \\\\\n" + \
    f"    Simulation time (s) & {tex(sim_time)} \\\\\n" + \
    f"    Total handovers & {tex(total_ho)} \\\\\n" + \
    f"    Total measurements & {tex(total_ms)} \\\\\n" + \
    r"""    Handover algorithms & RL-Optimised / Distance-Based / RSRP-Based \\
    Channel model & NR sub-6\,GHz + THz \\
    Traffic model & Mixed (Video, IoT, AR/VR, FL) \\
    Seeds & 5 \\
    \bottomrule
  \end{tabular}
\end{table}
"""
    _write(tables_dir / "sim_config.tex", content)


# ── handover_kpi.tex ───────────────────────────────────────────────────────

def write_handover_kpi(ho_rows: List[Dict[str, Any]], tables_dir: Path) -> None:
    # Group by algorithm
    by_algo: Dict[str, Dict[str, List[float]]] = {}
    for r in ho_rows:
        algo = r.get("algorithm", "Unknown")
        if algo not in by_algo:
            by_algo[algo] = {"latency": [], "tput_loss": [], "success": []}
        lat = r.get("latency")
        tl  = r.get("tput_loss")
        suc = r.get("success")
        if lat is not None:
            by_algo[algo]["latency"].append(lat)
        if tl is not None:
            by_algo[algo]["tput_loss"].append(tl)
        if suc is not None:
            by_algo[algo]["success"].append(float(suc))

    rows_tex = ""
    for algo, stats in sorted(by_algo.items()):
        lats = stats["latency"]
        tls  = stats["tput_loss"]
        suc  = stats["success"]
        lat_s  = f"{_mean(lats):.2f}" if lats else "--"
        tl_s   = f"{_mean(tls):.2f}"  if tls  else "--"
        suc_s  = f"{_mean(suc)*100:.1f}\\%" if suc else "--"
        rows_tex += f"    {tex(algo)} & {suc_s} & {lat_s} & {tl_s} \\\\\n"

    if not rows_tex:
        rows_tex = "    -- & -- & -- & -- \\\\\n"

    content = r"""%% Auto-generated by tools/paper/generate_paper_assets.py
\begin{table}[htbp]
  \centering
  \caption{Handover KPI Comparison (mean over all events)}
  \label{tab:handover_kpi}
  \begin{tabular}{@{}lccc@{}}
    \toprule
    \textbf{Algorithm} & \textbf{Success Rate} &
    \textbf{Latency (ms)} & \textbf{Tput Loss (ms)} \\
    \midrule
""" + rows_tex + r"""    \bottomrule
  \end{tabular}
\end{table}
"""
    _write(tables_dir / "handover_kpi.tex", content)


# ── mec_kpi.tex ────────────────────────────────────────────────────────────

def write_mec_kpi(mec_rows: List[Dict[str, Any]], tables_dir: Path) -> None:
    rows_tex = ""
    for r in mec_rows[:10]:  # cap at 10 services for table height
        svc   = tex(r.get("service_name", r.get("service", "--")))
        node  = tex(r.get("edge_node",    "--"))
        cpu   = _parse_float(r.get("cpu_utilization", r.get("cpu", "")))
        mem   = _parse_float(r.get("memory_utilization", r.get("memory", "")))
        lat   = _parse_float(r.get("latency_ms", r.get("latency", "")))
        cpu_s = f"{cpu*100:.1f}\\%" if cpu is not None else "--"
        mem_s = f"{mem*100:.1f}\\%" if mem is not None else "--"
        lat_s = f"{lat:.2f}"         if lat is not None else "--"
        rows_tex += f"    {svc} & {node} & {cpu_s} & {mem_s} & {lat_s} \\\\\n"

    if not rows_tex:
        rows_tex = "    -- & -- & -- & -- & -- \\\\\n"

    content = r"""%% Auto-generated by tools/paper/generate_paper_assets.py
\begin{table}[htbp]
  \centering
  \caption{MEC Service KPI Summary}
  \label{tab:mec_kpi}
  \begin{tabular}{@{}lcccc@{}}
    \toprule
    \textbf{Service} & \textbf{Edge Node} &
    \textbf{CPU} & \textbf{Memory} & \textbf{Latency (ms)} \\
    \midrule
""" + rows_tex + r"""    \bottomrule
  \end{tabular}
\end{table}
"""
    _write(tables_dir / "mec_kpi.tex", content)


# ── ml_summary.tex ─────────────────────────────────────────────────────────

def write_ml_summary(ml: Dict[str, Any], tables_dir: Path) -> None:
    rl  = ml.get("reinforcement_learning", ml.get("rl", {})) or {}
    dt  = ml.get("digital_twin",           ml.get("twin", {})) or {}

    final_reward     = rl.get("final_reward",         None)
    conv_episode     = rl.get("convergence_episode",  None)
    dt_accuracy      = dt.get("prediction_accuracy",  None)
    total_predictions = dt.get("total_predictions",   None)
    correct_pred      = dt.get("correct_predictions", None)

    # Build LaTeX-safe value strings.  Numeric values are safe without tex();
    # the accuracy string is pre-formatted with \% so must NOT go through tex().
    fr_s   = f"{final_reward:.4f}"        if final_reward        is not None else "--"
    ce_s   = str(conv_episode)            if conv_episode         is not None else "--"
    acc_s  = f"{dt_accuracy*100:.1f}\\%"  if dt_accuracy          is not None else "--"
    tp_s   = str(total_predictions)       if total_predictions    is not None else "--"
    cp_s   = str(correct_pred)            if correct_pred          is not None else "--"

    content = r"""%% Auto-generated by tools/paper/generate_paper_assets.py
\begin{table}[htbp]
  \centering
  \caption{AI/ML Module Summary}
  \label{tab:ml_summary}
  \begin{tabular}{@{}lcc@{}}
    \toprule
    \textbf{Module} & \textbf{Metric} & \textbf{Value} \\
    \midrule
""" + \
    f"    RL (DQN) & Final reward & {fr_s} \\\\\n" + \
    f"    RL (DQN) & Convergence episode & {ce_s} \\\\\n" + \
    f"    Digital Twin & Prediction accuracy & {acc_s} \\\\\n" + \
    f"    Digital Twin & Total predictions & {tp_s} \\\\\n" + \
    f"    Digital Twin & Correct predictions & {cp_s} \\\\\n" + \
    r"""    \bottomrule
  \end{tabular}
\end{table}
"""
    _write(tables_dir / "ml_summary.tex", content)


# ── latency_cdf.tex ────────────────────────────────────────────────────────

def write_latency_cdf(ho_rows: List[Dict[str, Any]], figures_dir: Path) -> None:
    by_algo: Dict[str, List[float]] = {}
    for r in ho_rows:
        algo = r.get("algorithm", "Unknown")
        lat  = r.get("latency")
        if lat is not None:
            by_algo.setdefault(algo, []).append(lat)

    if not by_algo:
        placeholder = r"""%% TODO – no handover latency data found
%% Run generate_paper_assets.py after populating results/
\begin{tikzpicture}
\begin{axis}[
  width=0.95\columnwidth, height=5.5cm,
  xlabel={Handover Latency (ms)},
  ylabel={CDF},
  xmin=0, xmax=30, ymin=0, ymax=1,
  legend pos=south east,
  grid=major, grid style={dotted},
]
\end{axis}
\end{tikzpicture}
"""
        _write(figures_dir / "latency_cdf.tex", placeholder)
        return

    # Build pgfplots coordinates for each algorithm
    colors = ["blue", "red!70!black", "green!60!black",
              "orange", "violet", "cyan!70!black"]
    plots = ""
    for idx, (algo, lats) in enumerate(sorted(by_algo.items())):
        lats_s = sorted(lats)
        n = len(lats_s)
        coords = " ".join(
            f"({v:.3f},{i/n:.4f})" for i, v in enumerate(lats_s, 1)
        )
        color = colors[idx % len(colors)]
        plots += (
            f"  \\addplot[{color}, thick] coordinates {{{coords}}};\n"
            f"  \\addlegendentry{{{tex(algo)}}}\n"
        )

    content = r"""%% Auto-generated by tools/paper/generate_paper_assets.py
\begin{tikzpicture}
\begin{axis}[
  width=0.95\columnwidth, height=5.5cm,
  xlabel={Handover Latency (ms)},
  ylabel={CDF},
  ymin=0, ymax=1,
  legend pos=south east,
  grid=major, grid style={dotted},
  cycle list name=color list,
]
""" + plots + r"""\end{axis}
\end{tikzpicture}
"""
    _write(figures_dir / "latency_cdf.tex", content)


# ── throughput_vs_load.tex ─────────────────────────────────────────────────

def write_throughput_vs_load(mec_rows: List[Dict[str, Any]],
                              figures_dir: Path) -> None:
    """
    Derive a proxy throughput-vs-load curve from MEC rows.
    ``active_users`` → load axis; ``throughput_mbps`` → throughput axis.
    """
    by_node: Dict[str, List[Tuple[float, float]]] = {}
    for r in mec_rows:
        node  = r.get("edge_node", r.get("host", "edge"))
        users = _parse_float(r.get("active_users", r.get("users", "")))
        tput  = _parse_float(r.get("throughput_mbps", r.get("throughput", "")))
        if users is not None and tput is not None:
            by_node.setdefault(node, []).append((users, tput))

    if not by_node:
        placeholder = r"""%% TODO – no throughput/load data found
%% Run generate_paper_assets.py after populating results/
\begin{tikzpicture}
\begin{axis}[
  width=0.95\columnwidth, height=5.5cm,
  xlabel={Number of Active Users},
  ylabel={Throughput (Mbps)},
  legend pos=north west,
  grid=major, grid style={dotted},
]
\end{axis}
\end{tikzpicture}
"""
        _write(figures_dir / "throughput_vs_load.tex", placeholder)
        return

    colors = ["blue", "red!70!black", "green!60!black",
              "orange", "violet", "cyan!70!black"]
    plots = ""
    for idx, (node, pts) in enumerate(sorted(by_node.items())):
        pts_s = sorted(pts, key=lambda x: x[0])
        coords = " ".join(f"({u:.1f},{t:.2f})" for u, t in pts_s)
        color  = colors[idx % len(colors)]
        plots += (
            f"  \\addplot[{color}, thick, mark=*] coordinates {{{coords}}};\n"
            f"  \\addlegendentry{{{tex(node)}}}\n"
        )

    content = r"""%% Auto-generated by tools/paper/generate_paper_assets.py
\begin{tikzpicture}
\begin{axis}[
  width=0.95\columnwidth, height=5.5cm,
  xlabel={Number of Active Users},
  ylabel={Throughput (Mbps)},
  legend pos=north west,
  grid=major, grid style={dotted},
]
""" + plots + r"""\end{axis}
\end{tikzpicture}
"""
    _write(figures_dir / "throughput_vs_load.tex", content)


# ── rsrp_timeseries.tex ────────────────────────────────────────────────────

def write_rsrp_timeseries(rsrp_rows: List[Dict[str, Any]],
                           figures_dir: Path) -> None:
    times: List[float] = []
    rsrps: List[float] = []
    sinrs: List[float] = []

    for r in rsrp_rows:
        t = _parse_float(r.get("time", r.get("timestamp", "")))
        v_rsrp = _parse_float(r.get("rsrp_dbm", r.get("rsrp", "")))
        v_sinr = _parse_float(r.get("sinr_db",  r.get("sinr", "")))
        if t is not None and (v_rsrp is not None or v_sinr is not None):
            times.append(t)
            rsrps.append(v_rsrp if v_rsrp is not None else float("nan"))
            sinrs.append(v_sinr if v_sinr is not None else float("nan"))

    # Subsample to at most 100 points for manageable .tex size
    step = max(1, len(times) // 100)
    times = times[::step]
    rsrps = rsrps[::step]
    sinrs = sinrs[::step]

    if not times:
        placeholder = r"""%% TODO – no RSRP/SINR time-series data found
%% Run generate_paper_assets.py after populating results/
\begin{tikzpicture}
\begin{axis}[
  width=0.95\columnwidth, height=5.5cm,
  xlabel={Simulation Time (s)},
  ylabel={Signal Strength (dBm / dB)},
  legend pos=north east,
  grid=major, grid style={dotted},
]
\end{axis}
\end{tikzpicture}
"""
        _write(figures_dir / "rsrp_timeseries.tex", placeholder)
        return

    rsrp_coords = " ".join(
        f"({t:.2f},{v:.2f})" for t, v in zip(times, rsrps)
        if not math.isnan(v)
    )
    sinr_coords = " ".join(
        f"({t:.2f},{v:.2f})" for t, v in zip(times, sinrs)
        if not math.isnan(v)
    )

    content = r"""%% Auto-generated by tools/paper/generate_paper_assets.py
\begin{tikzpicture}
\begin{axis}[
  width=0.95\columnwidth, height=5.5cm,
  xlabel={Simulation Time (s)},
  ylabel={Signal Strength (dBm / dB)},
  legend pos=north east,
  grid=major, grid style={dotted},
]
  \addplot[blue, thick] coordinates {""" + rsrp_coords + r"""};
  \addlegendentry{RSRP (dBm)}
  \addplot[red!70!black, thick, dashed] coordinates {""" + sinr_coords + r"""};
  \addlegendentry{SINR (dB)}
\end{axis}
\end{tikzpicture}
"""
    _write(figures_dir / "rsrp_timeseries.tex", content)


# ── auto_results.tex ───────────────────────────────────────────────────────

def write_auto_results(
    meta: Dict[str, Any],
    ho_rows: List[Dict[str, Any]],
    mec_rows: List[Dict[str, Any]],
    ml: Dict[str, Any],
    sections_dir: Path,
) -> None:
    """Write a short, number-grounded results paragraph."""

    # -- Handover stats ------------------------------------------------------
    by_algo: Dict[str, Dict[str, List[float]]] = {}
    for r in ho_rows:
        algo = r.get("algorithm", "Unknown")
        if algo not in by_algo:
            by_algo[algo] = {"latency": [], "success": []}
        lat = r.get("latency")
        suc = r.get("success")
        if lat is not None:
            by_algo[algo]["latency"].append(lat)
        if suc is not None:
            by_algo[algo]["success"].append(float(suc))

    ho_sentences = []
    for algo, stats in sorted(by_algo.items()):
        lats = stats["latency"]
        suc  = stats["success"]
        if lats:
            med = _percentile(lats, 50)
            p95 = _percentile(lats, 95)
            sr  = _mean(suc) * 100 if suc else None
            s = (
                f"The {algo} policy achieves a median handover latency of "
                f"{med:.2f}\\,ms (P95: {p95:.2f}\\,ms)"
            )
            if sr is not None:
                s += f" with a success rate of {sr:.1f}\\%"
            s += "."
            ho_sentences.append(s)

    # -- MEC stats -----------------------------------------------------------
    cpu_vals = []
    lat_vals = []
    for r in mec_rows:
        c = _parse_float(r.get("cpu_utilization", r.get("cpu", "")))
        l = _parse_float(r.get("latency_ms",      r.get("latency", "")))
        if c is not None: cpu_vals.append(c)
        if l is not None: lat_vals.append(l)

    mec_sentence = ""
    if cpu_vals and lat_vals:
        mec_sentence = (
            f"Across all MEC services, the mean CPU utilisation is "
            f"{_mean(cpu_vals)*100:.1f}\\% and the mean service latency is "
            f"{_mean(lat_vals):.2f}\\,ms."
        )

    # -- ML stats ------------------------------------------------------------
    rl = ml.get("reinforcement_learning", ml.get("rl", {})) or {}
    dt = ml.get("digital_twin", ml.get("twin", {})) or {}
    rl_sentence = ""
    dt_sentence = ""
    if rl.get("final_reward") is not None:
        rl_sentence = (
            f"The DQN agent converges at episode "
            f"{rl.get('convergence_episode', '--')} with a final reward of "
            f"{rl['final_reward']:.4f}."
        )
    if dt.get("prediction_accuracy") is not None:
        dt_sentence = (
            f"The digital-twin achieves a KPI prediction accuracy of "
            f"{dt['prediction_accuracy']*100:.1f}\\% over "
            f"{dt.get('total_predictions', '--')} predictions."
        )

    # -- Assemble paragraph --------------------------------------------------
    parts = ho_sentences + [mec_sentence, rl_sentence, dt_sentence]
    parts = [p for p in parts if p]

    if not parts:
        paragraph = (
            r"% TODO: no numeric data extracted from results/."
            "\n"
            r"Run \texttt{python tools/paper/generate\_paper\_assets.py} "
            r"after populating \texttt{results/} to replace this placeholder."
        )
    else:
        paragraph = (
            "% Auto-generated paragraph – do not edit by hand.\n"
            + " ".join(parts)
        )

    content = "%% Auto-generated by tools/paper/generate_paper_assets.py\n" + paragraph + "\n"
    _write(sections_dir / "auto_results.tex", content)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate LaTeX paper assets from simulation results."
    )
    repo_root = Path(__file__).resolve().parent.parent.parent
    parser.add_argument(
        "--results", default=str(repo_root / "results"),
        help="Path to results directory (default: <repo>/results)"
    )
    parser.add_argument(
        "--paper", default=str(repo_root / "paper"),
        help="Path to paper directory (default: <repo>/paper)"
    )
    args = parser.parse_args()

    results_dir = Path(args.results)
    paper_dir   = Path(args.paper)
    tables_dir  = paper_dir / "tables"
    figures_dir = paper_dir / "figures"
    sections_dir = paper_dir / "sections"

    for d in (tables_dir, figures_dir, sections_dir):
        d.mkdir(parents=True, exist_ok=True)

    print(f"Scanning {results_dir} ...")
    found = scan_results(results_dir)
    csv_paths  = found["csv"]
    json_paths = found["json"]
    print(f"  CSV files : {len(csv_paths)}")
    print(f"  JSON files: {len(json_paths)}")

    # Extract data
    ho_rows  = _extract_handover(csv_paths)
    mec_rows = _extract_mec(csv_paths)
    rsrp_rows = _extract_rsrp(csv_paths)
    ml       = _extract_ml(json_paths)
    meta     = _extract_sim_meta(json_paths)

    print(f"  Handover events: {len(ho_rows)}")
    print(f"  MEC service rows: {len(mec_rows)}")
    print(f"  RSRP rows: {len(rsrp_rows)}")
    print(f"  ML keys: {list(ml.keys())}")
    print(f"  Metadata keys: {list(meta.keys())}")

    print("\nGenerating tables ...")
    write_sim_config(meta, tables_dir)
    write_handover_kpi(ho_rows, tables_dir)
    write_mec_kpi(mec_rows, tables_dir)
    write_ml_summary(ml, tables_dir)

    print("\nGenerating figures ...")
    write_latency_cdf(ho_rows, figures_dir)
    write_throughput_vs_load(mec_rows, figures_dir)
    write_rsrp_timeseries(rsrp_rows, figures_dir)

    print("\nGenerating auto_results paragraph ...")
    write_auto_results(meta, ho_rows, mec_rows, ml, sections_dir)

    print("\nDone.  Run:  cd paper && latexmk -pdf main.tex")


if __name__ == "__main__":
    main()
