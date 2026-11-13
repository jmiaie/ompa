#!/usr/bin/env python3
"""Regenerate the README infographics (SVG, light + dark) from the published benchmark files.

Usage (from the repo root):  python docs/assets/make_assets.py
Needs: numpy, matplotlib. No network, no LLM calls. Every number drawn here is read from a file under
benchmarks/published/; nothing is typed in by hand except labels. The 95% CIs on head-to-head MRR are
recomputed from the per-question rows with the same paired-bootstrap settings as the published analysis
(B=10,000, seed 20261020, percentile), and the point estimates are checked against summary.json.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "assets"
PUB = ROOT / "benchmarks" / "published"
H2H = PUB / "2026-10-24-headtohead"
LME500 = PUB / "2026-11-12-lme500" / "summary500__locus_ompa.json"
RANKING = PUB / "2026-11-12-ranking" / "summary__locus.json"  # Locus repo only
B, SEED = 10_000, 20261020

_name = re.search(r'^name\s*=\s*"([^"]+)"', (ROOT / "pyproject.toml").read_text(), re.M).group(1)
REPO = "locus" if _name.startswith("locus") else "ompa"

THEMES = {
    "light": dict(fg="#1f2328", muted="#59636e", grid="#d1d9e0", other="#8c959f", panel="#f6f8fa", border="#d1d9e0"),
    "dark": dict(fg="#e6edf3", muted="#9198a1", grid="#3d444d", other="#6e7681", panel="#161b22", border="#3d444d"),
}
COLORS = {"locus": "#2f81f7", "ompa": "#a371f7"}
LIGHTER = {"locus": "#79b8ff", "ompa": "#d2a8ff"}
FONT = "Inter, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

plt.rcParams.update({"svg.fonttype": "none", "font.family": "sans-serif",
                     "font.sans-serif": ["Helvetica", "Arial", "DejaVu Sans"], "font.size": 11})


# ----------------------------------------------------------------------------- data
def r3(v):
    """Round half-up to 3 decimals, as in the published tables (0.9325 -> 0.933)."""
    from decimal import ROUND_HALF_UP, Decimal
    return str(Decimal(str(v)).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP))


def load_h2h():
    summ = json.loads((H2H / "summary.json").read_text())
    sub = json.loads((H2H / "scripts" / "lme_subset30.json").read_text())

    def rows(system):
        fname = {"letta_direct": "letta", "graphiti_relaxed": "graphiti"}.get(system, system)
        out = {}
        for line in open(H2H / "results" / f"lme30__{fname}.jsonl"):
            r = json.loads(line)
            if "error" in r:
                continue
            if system == "letta_direct":
                r = {**r, "ranked": r["direct_ranked"]}
            if system == "graphiti_relaxed":
                if "relaxed_ranked" not in r:
                    continue
                r = {**r, "ranked": r["relaxed_ranked"]}
            out[r["qid"]] = r
        return out

    def rr(r):
        ans = set(r["answers"])
        for i, d in enumerate(r["ranked"][:10]):
            if d in ans:
                return 1 / (i + 1)
        return 0.0

    res = {}
    for s in ["locus", "ompa", "mem0", "langmem", "graphiti", "graphiti_relaxed", "letta", "letta_direct"]:
        rw = rows(s)
        x = np.array([rr(rw[q]) for q in sub if q in rw])
        m = x[np.random.default_rng(SEED).integers(0, len(x), (B, len(x)))].mean(1)
        mrr = round(float(x.mean()), 3)
        assert mrr == summ[s]["MRR@10"], (s, mrr, summ[s]["MRR@10"])
        assert len(x) == summ[s]["n"]
        res[s] = {**summ[s], "MRR_ci": [float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))]}
    return res


# ----------------------------------------------------------------------------- helpers
def style(ax, t):
    ax.set_facecolor("none")
    for sp in ("top", "right", "left"):
        ax.spines[sp].set_visible(False)
    ax.spines["bottom"].set_color(t["grid"])
    ax.tick_params(colors=t["muted"], length=0)
    ax.xaxis.grid(True, color=t["grid"], lw=0.6)
    ax.set_axisbelow(True)


def save(fig, name, theme):
    fig.patch.set_alpha(0)
    path = OUT / f"{name}-{theme}.svg"
    fig.savefig(path, format="svg", transparent=True, bbox_inches="tight", pad_inches=0.15,
                metadata={"Date": None, "Creator": "make_assets.py"})
    plt.close(fig)
    # stable output: strip matplotlib's random clip-path / glyph ids
    txt = path.read_text()
    ids = {}
    for m in re.finditer(r'id="([a-z0-9]{6,})"', txt):
        ids.setdefault(m.group(1), f"{name[:3]}{len(ids)}")
    for old, new in ids.items():
        txt = txt.replace(old, new)
    path.write_text(txt)
    return path


LABELS = {
    "locus": "Locus (BM25, no LLM)", "ompa": "OMPA (BM25 + MiniLM, no LLM)",
    "mem0": "mem0 2.2.1", "langmem": "LangMem 0.0.30", "graphiti": "Graphiti 0.31.2 (Zep OSS)",
    "graphiti_relaxed": "Graphiti, cosine floor removed", "letta": "Letta 0.16.8 agent",
    "letta_direct": "Letta archival-search API",
}


def color_for(s, t):
    if s in COLORS:
        return COLORS[s] if s == REPO else LIGHTER[s]
    return t["other"]


# ----------------------------------------------------------------------------- charts
def chart_headtohead(h, theme):
    t = THEMES[theme]
    order = sorted(h, key=lambda s: h[s]["MRR@10"])
    fig, ax = plt.subplots(figsize=(9, 4.6))
    style(ax, t)
    y = np.arange(len(order))
    vals = [h[s]["MRR@10"] for s in order]
    lo = [h[s]["MRR@10"] - h[s]["MRR_ci"][0] for s in order]
    hi = [h[s]["MRR_ci"][1] - h[s]["MRR@10"] for s in order]
    ax.barh(y, vals, color=[color_for(s, t) for s in order], height=0.62)
    ax.errorbar(vals, y, xerr=[lo, hi], fmt="none", ecolor=t["fg"], elinewidth=1, capsize=3, alpha=0.75)
    for yi, s in zip(y, order):
        bold = s in ("locus", "ompa")
        ax.text(1.02, yi, f"{h[s]['MRR@10']:.3f}", va="center", ha="left", color=t["fg"],
                fontweight="bold" if bold else "normal", transform=ax.get_yaxis_transform())
        ax.text(1.12, yi, f"{h[s]['R@5']:.2f}", va="center", ha="left", color=t["muted"],
                transform=ax.get_yaxis_transform())
    ax.text(1.02, len(order) - 0.35, "MRR", ha="left", color=t["muted"], fontsize=9, transform=ax.get_yaxis_transform())
    ax.text(1.12, len(order) - 0.35, "R@5", ha="left", color=t["muted"], fontsize=9, transform=ax.get_yaxis_transform())
    ax.set_yticks(y, [LABELS[s] for s in order], color=t["fg"])
    for lab, s in zip(ax.get_yticklabels(), order):
        if s in ("locus", "ompa"):
            lab.set_fontweight("bold")
    ax.set_xlim(0, 1)
    ax.set_xlabel("MRR@10 with 95% bootstrap CI (higher is better)", color=t["muted"])
    ax.set_title("Session retrieval on LongMemEval-S: same 30 questions, same harness",
                 loc="left", color=t["fg"], fontsize=13, fontweight="bold", pad=22)
    ax.text(0, 1.02, "LLM-based systems use gpt-4o-mini; all use local MiniLM embeddings. n=30 (low power).",
            transform=ax.transAxes, color=t["muted"], fontsize=9.5)
    return save(fig, "headtohead", theme)


def chart_efficiency(h, theme):
    t = THEMES[theme]
    systems = ["locus", "ompa", "mem0", "langmem", "graphiti", "letta"]
    names = {"locus": "Locus", "ompa": "OMPA", "mem0": "mem0", "langmem": "LangMem", "graphiti": "Graphiti",
             "letta": "Letta agent"}
    n = {s: h[s]["n"] for s in systems}
    panels = [
        ("LLM calls per question", {s: h[s]["llm_calls"] / n[s] for s in systems}, "{:.0f}", (0.5, 2000)),
        ("LLM cost per question, USD (upper bound)",
         {s: h[s].get("usd_per_question") or 0 for s in systems}, "${:.4f}", (2e-4, 3)),
        ("Ingest time per ~48-session history, s (p50)",
         {s: h[s]["ingest_p50_s_per_haystack"] for s in systems}, "{:g} s", (0.1, 5000)),
        ("Query latency, ms (p50)", {s: h[s]["query_p50_ms"] for s in systems}, "{:g} ms", (1, 40000)),
    ]
    fig, axes = plt.subplots(2, 2, figsize=(10, 5.6))
    order = list(reversed(systems))
    for ax, (title, d, fmt, xl) in zip(axes.flat, panels):
        style(ax, t)
        ax.set_xscale("log")
        ax.set_xlim(*xl)
        y = np.arange(len(order))
        for yi, s in zip(y, order):
            v = d[s]
            c = color_for(s, t)
            if v == 0:
                ax.text(xl[0] * 1.3, yi, "0  (no LLM)", va="center", ha="left", color=c, fontweight="bold")
                continue
            ax.barh(yi, v - xl[0], color=c, height=0.6, left=xl[0])
            lab = fmt.format(v)
            if fmt == "{:.0f}" and v < 10:
                lab = f"{v:.1f}"
            ax.text(v * 1.25, yi, lab, va="center", ha="left", color=t["fg"], fontsize=9.5,
                    fontweight="bold" if s in ("locus", "ompa") else "normal")
        ax.set_yticks(y, [names[s] for s in order], color=t["fg"])
        for lab, s in zip(ax.get_yticklabels(), order):
            if s in ("locus", "ompa"):
                lab.set_fontweight("bold")
        ax.set_title(title, loc="left", color=t["fg"], fontsize=11, fontweight="bold")
        ax.tick_params(axis="x", labelsize=8.5)
        ax.xaxis.set_minor_locator(matplotlib.ticker.NullLocator())
    fig.suptitle("What it costs to remember: same 30 LongMemEval-S questions (log scales)",
                 x=0.01, ha="left", color=t["fg"], fontsize=13, fontweight="bold")
    fig.text(0.01, 0.005, "LLM: gpt-4o-mini via OpenRouter; cost and calls from gateway logs, including pilot runs, "
             "so they are upper bounds. Letta calls the LLM at query time; the others at ingest. Shared VM; "
             "latencies indicative.", color=t["muted"], fontsize=8.5, ha="left", wrap=True)
    fig.tight_layout(rect=(0, 0.04, 1, 0.96))
    return save(fig, "efficiency", theme)


TYPES = ["knowledge-update", "multi-session", "single-session-assistant", "single-session-preference",
         "single-session-user", "temporal-reasoning"]
TYPE_LABEL = {"knowledge-update": "knowledge\nupdate", "multi-session": "multi-\nsession",
              "single-session-assistant": "assistant\nfact", "single-session-preference": "preference",
              "single-session-user": "user\nfact", "temporal-reasoning": "temporal\nreasoning"}


def chart_by_type(theme):
    t = THEMES[theme]
    s5 = json.loads(LME500.read_text())["non_abstention_470"]
    series = []  # (label, color, overall, overall_ci, per-type vals, per-type ci or None)

    def base(sys_):
        d = s5[sys_]
        return (d["MRR@10"], d["MRR@10_ci"], [d["by_type"][k]["MRR@10"] for k in TYPES],
                [d["by_type"][k]["MRR_ci"] for k in TYPES], [d["by_type"][k]["n"] for k in TYPES])

    lo_, loci, lt, ltci, ns = base("locus")
    om, omci, ot, otci, _ = base("ompa")
    if REPO == "locus":
        rk = json.loads(RANKING.read_text())
        assert rk["variants"]["baseline"]["MRR@10"] == lo_
        dt = rk["variants"]["dense_temporal"]
        series = [("Locus default (BM25)", COLORS["locus"], lo_, loci, lt, ltci),
                  ("Locus + DenseReranker + temporal (opt-in)", "#1a7f37" if theme == "light" else "#3fb950",
                   dt["MRR@10"], dt["MRR_ci"], [dt["by_type"][k]["MRR@10"] for k in TYPES], None),
                  ("OMPA (hybrid)", LIGHTER["ompa"], om, omci, ot, otci)]
    else:
        series = [("OMPA (hybrid BM25 + MiniLM)", COLORS["ompa"], om, omci, ot, otci),
                  ("Locus (BM25)", LIGHTER["locus"], lo_, loci, lt, ltci)]
    groups = ["All 470"] + [f"{TYPE_LABEL[k]}\n(n={n})" for k, n in zip(TYPES, ns)]
    fig, ax = plt.subplots(figsize=(10, 4.4))
    style(ax, t)
    ax.xaxis.grid(False)
    ax.yaxis.grid(True, color=t["grid"], lw=0.6)
    x = np.arange(len(groups))
    w = 0.8 / len(series)
    for i, (lab, c, ov, ovci, vals, cis) in enumerate(series):
        v = [ov] + vals
        xs = x - 0.4 + w / 2 + i * w
        ax.bar(xs, v, w * 0.92, color=c, label=lab)
        cil = [ovci] + (cis if cis else [None] * len(vals))
        for xi, vi, ci in zip(xs, v, cil):
            if ci:
                ax.errorbar([xi], [vi], yerr=[[vi - ci[0]], [ci[1] - vi]], fmt="none", ecolor=t["fg"],
                            elinewidth=0.9, capsize=2, alpha=0.7)
        ax.text(xs[0], ov - 0.015, r3(ov), ha="center", va="top", rotation=90, color="white",
                fontsize=9, fontweight="bold")
    ax.set_xticks(x, groups, color=t["fg"], fontsize=9)
    ax.set_ylim(0.4, 1.05)
    ax.set_ylabel("MRR@10", color=t["muted"])
    leg = ax.legend(loc="upper left", bbox_to_anchor=(0, -0.2), frameon=False, fontsize=9.5, ncol=len(series))
    for txt in leg.get_texts():
        txt.set_color(t["fg"])
    ax.set_title("LongMemEval-S, all 470 non-abstention questions, by question type",
                 loc="left", color=t["fg"], fontsize=13, fontweight="bold", pad=22)
    note = "95% bootstrap CIs where published (B=10,000). Local MiniLM embeddings, 0 LLM calls. y-axis starts at 0.4."
    if REPO == "locus":
        note = ("95% bootstrap CIs where published; the opt-in series has an overall CI only. "
                "0 LLM calls. y-axis starts at 0.4.")
    ax.text(0, 1.02, note, transform=ax.transAxes, color=t["muted"], fontsize=9)
    return save(fig, "by-type", theme)


# ----------------------------------------------------------------------------- hand-built SVGs
def esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def mem0_delta(h, ref):
    d = h["mem0"][f"vs_{ref}"]  # mem0 minus ref, paired bootstrap on the same 30 questions
    return (f"{-d['diff']:+.3f}", "MRR@10 vs mem0", "same 30 q, " + ("significant" if d["sig"] else "not significant"))


def hero(theme):
    t = THEMES[theme]
    s5 = json.loads(LME500.read_text())["non_abstention_470"][REPO]
    h = json.loads((H2H / "summary.json").read_text())
    acc = COLORS[REPO]
    if REPO == "locus":
        title, tag = "Locus", "Explainable, vectorless retrieval for AI agents. MCP-native. Zero dependencies."
        stats = [(r3(s5['R@5']), "R@5 on LongMemEval-S", "470 questions"),
                 (f"{s5['query_ms_p50']:.1f} ms", "query p50", "~48-session history"),
                 ("0", "LLM calls to index or search", "$0 per question"),
                 mem0_delta(h, "locus")]
    else:
        title, tag = "OMPA", "Persistent memory for any AI agent: verbatim vault, temporal knowledge graph, MCP."
        stats = [(r3(s5['R@5']), "R@5 on LongMemEval-S", "470 questions"),
                 (r3(s5['MRR@10']), "MRR@10", "470 questions"),
                 ("0", "LLM calls to store or search", "$0 per question"),
                 mem0_delta(h, "ompa")]
    W, H = 1200, 300
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
             f'font-family="{FONT}" role="img" aria-label="{esc(title)}: {esc(tag)}">',
             f'<rect x="1" y="1" width="{W-2}" height="{H-2}" rx="18" fill="{t["panel"]}" stroke="{t["border"]}"/>',
             f'<rect x="40" y="44" width="8" height="64" rx="4" fill="{acc}"/>',
             f'<text x="66" y="96" font-size="58" font-weight="800" fill="{t["fg"]}">{esc(title)}</text>',
             f'<text x="66" y="136" font-size="21" fill="{t["muted"]}">{esc(tag)}</text>']
    tw, gap, x0, y0 = 262, 18, 40, 168
    for i, (big, lab, sub) in enumerate(stats):
        x = x0 + i * (tw + gap)
        parts += [f'<rect x="{x}" y="{y0}" width="{tw}" height="96" rx="12" fill="none" stroke="{t["border"]}"/>',
                  f'<text x="{x+18}" y="{y0+44}" font-size="32" font-weight="700" fill="{acc}">{esc(big)}</text>',
                  f'<text x="{x+18}" y="{y0+68}" font-size="15" fill="{t["fg"]}">{esc(lab)}</text>',
                  f'<text x="{x+18}" y="{y0+86}" font-size="12.5" fill="{t["muted"]}">{esc(sub)}</text>']
    parts.append("</svg>")
    p = OUT / f"hero-{theme}.svg"
    p.write_text("\n".join(parts) + "\n")
    return p


def box(x, y, w, h, title, lines, t, accent=None, dashed=False):
    stroke = accent or t["border"]
    dash = ' stroke-dasharray="6 4"' if dashed else ""
    out = [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="none" stroke="{stroke}" '
           f'stroke-width="{2 if accent else 1.2}"{dash}/>',
           f'<text x="{x+14}" y="{y+24}" font-size="15" font-weight="700" fill="{t["fg"]}">{esc(title)}</text>']
    for i, ln in enumerate(lines):
        out.append(f'<text x="{x+14}" y="{y+46+i*19}" font-size="13" fill="{t["muted"]}">{esc(ln)}</text>')
    return out


def arrow(x1, y1, x2, y2, t, label=None):
    out = [f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{t["muted"]}" stroke-width="1.6" '
           f'marker-end="url(#ah)"/>']
    if label:
        out.append(f'<text x="{(x1+x2)/2}" y="{min(y1,y2)-6 if y1==y2 else (y1+y2)/2}" font-size="12" '
                   f'text-anchor="middle" fill="{t["muted"]}">{esc(label)}</text>')
    return out


def architecture(theme):
    t = THEMES[theme]
    acc = COLORS[REPO]
    W, H = 1200, 400
    p = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" '
         f'font-family="{FONT}" role="img" aria-label="{REPO} architecture">',
         f'<defs><marker id="ah" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
         f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{t["muted"]}"/></marker></defs>']
    if REPO == "locus":
        p += box(20, 30, 230, 150, "Your agent / app", ["Claude Code, Cursor, Codex,", "Claude Desktop, scripts"], t)
        p += box(20, 220, 230, 150, "Interfaces", ["MCP stdio: 47 tools, 4 prompts", "CLI: locus ...", "Python: LocusEngine",
                                                   "HTTP JSON-RPC (token)"], t)
        p += arrow(135, 180, 135, 218, t)
        p += box(300, 30, 270, 150, "Stage 1: BM25 (default)", ["SQLite FTS5, Porter + stop-words",
                                                                  "deterministic, read-only", "~2 ms per query"], t, acc)
        p += box(620, 30, 270, 150, "Stage 2: optional re-rank", ["mode=\"hybrid\": KG, link walk,",
                                                                    "structure, recency, popularity",
                                                                    "fused by weighted RRF"], t, dashed=True)
        p += box(620, 220, 270, 150, "Opt-in 1.2 ranking", ["DenseReranker (locus-rag[dense])",
                                                            "route=\"advice\" (post hoc)",
                                                            "temporal=True date windows"], t, dashed=True)
        p += box(940, 30, 240, 150, "Results + provenance", ["via=bm25 / kg / link / time", "explain(): matched terms,",
                                                              "entities, KG facts"], t, acc)
        p += box(300, 220, 270, 150, "Memory layer (SQLite)", ["corpus + chunker, checksum dedup",
                                                               "temporal KG with validity windows",
                                                               "entity aliases, OMPA bridge"], t)
        p += arrow(250, 105, 298, 105, t)
        p += arrow(570, 105, 618, 105, t)
        p += arrow(890, 105, 938, 105, t)
        p += arrow(435, 220, 435, 182, t)
        p += arrow(755, 220, 755, 182, t)
        p.append(f'<text x="940" y="250" font-size="13" fill="{t["muted"]}">No embeddings, GPU or LLM</text>')
        p.append(f'<text x="940" y="270" font-size="13" fill="{t["muted"]}">on the default path.</text>')
        p.append(f'<text x="940" y="300" font-size="13" fill="{t["muted"]}">Dashed = off by default.</text>')
    else:
        p += box(20, 30, 250, 150, "Your agent", ["Claude Code, Codex, Gemini CLI,", "OpenClaw, LangChain, custom"], t)
        p += box(20, 220, 250, 150, "Interfaces", ["MCP server: 24 tools (ao-mcp)", "CLI: ao ...", "Python: Ompa(...)",
                                                   "5 lifecycle hooks"], t)
        p += arrow(145, 180, 145, 218, t)
        p += box(330, 30, 260, 110, "Layer 1: Vault", ["verbatim markdown notes", "Obsidian-compatible folders"], t, acc)
        p += box(330, 150, 260, 110, "Layer 2: Palace", ["wings, rooms, drawers,", "halls and tunnels"], t, acc)
        p += box(330, 270, 260, 110, "Layer 3: Temporal KG", ["SQLite triples with", "validity windows, as_of"], t, acc)
        p += box(650, 30, 270, 150, "Search", ["SQLite FTS5 keyword (BM25)", "+ local MiniLM (ompa[semantic])",
                                               "fused by RRF; temporal=True", "date windows (opt-in)"], t)
        p += box(650, 220, 270, 150, "Audit memory (opt-in)", ["hash-chained log, HMAC / Ed25519",
                                                               "provenance citations, as_of,",
                                                               "retention, erasure, ACLs"], t, dashed=True)
        p += box(980, 30, 200, 150, "Results", ["path + line citation", "content hash", "valid_from / stale"], t, acc)
        p += arrow(270, 105, 328, 85, t)
        p += arrow(590, 85, 648, 95, t)
        p += arrow(920, 105, 978, 105, t)
        p.append(f'<text x="980" y="250" font-size="13" fill="{t["muted"]}">No LLM to store or search.</text>')
        p.append(f'<text x="980" y="270" font-size="13" fill="{t["muted"]}">Keyword search needs no</text>')
        p.append(f'<text x="980" y="288" font-size="13" fill="{t["muted"]}">ML dependencies.</text>')
        p.append(f'<text x="980" y="318" font-size="13" fill="{t["muted"]}">Dashed = off by default.</text>')
    p.append("</svg>")
    path = OUT / f"architecture-{theme}.svg"
    path.write_text("\n".join(p) + "\n")
    return path


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    h = load_h2h()
    made = []
    for theme in THEMES:
        made += [hero(theme), architecture(theme), chart_headtohead(h, theme), chart_efficiency(h, theme),
                 chart_by_type(theme)]
    for m in made:
        print(m.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
