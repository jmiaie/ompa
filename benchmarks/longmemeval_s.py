"""
LongMemEval-S session-level retrieval benchmark for OMPA search.

Protocol (one fresh vault per question):
  1. every haystack session of the question is written as one note
     (``ao.write``, "role: content" lines, user + assistant turns);
  2. ``ao.rebuild_index()``;
  3. the question is the query; we record the top-10 note ids for each mode:
       hybrid   - ``ao.search(q)``                (BM25 + semantic, RRF; default)
       semantic - ``ao.search(q, hybrid=False)``  (embeddings only)
       keyword  - ``ao.keyword.search(q)``        (SQLite FTS5 BM25 only; no extra needed)
  4. metrics: recall_any@k = at least one evidence session in the top k;
     recall_all@5; nDCG@10.  95% CIs by bootstrap over questions
     (10,000 resamples, seed 20261020).  The 30 abstention (``_abs``) questions
     have no evidence session and are excluded -> n = 470.

Data: ``longmemeval_s_cleaned.json`` from
https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned

Usage:
    pip install "ompa[semantic]"
    python benchmarks/longmemeval_s.py run --data longmemeval_s_cleaned.json \\
        --out lme_raw_shard0.jsonl --shard 0 --nshards 2      # and shard 1, ...
    python benchmarks/longmemeval_s.py analyze lme_raw_shard*.jsonl

``run`` appends and skips question ids already in ``--out``, so it can resume.
Memory: loading the 270 MB JSON array takes ~2-3 GB of RAM per process; install
``ijson`` (streamed) or convert it to JSONL first to stay around ~1.2 GB per shard.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import math
import re
import tempfile
import time
from pathlib import Path

B = 10_000
SEED = 20261020
MODES = ("hybrid", "semantic", "keyword")


def _iter_questions(path: Path):
    """Yield non-abstention questions in file order (JSON array or JSONL)."""
    if path.suffix == ".jsonl":
        with path.open() as f:
            for line in f:
                if line.strip():
                    e = json.loads(line)
                    if not e["question_id"].endswith("_abs"):
                        yield e
        return
    try:
        import ijson  # optional: streams the 270 MB array without loading it

        with path.open("rb") as f:
            for e in ijson.items(f, "item"):
                if not e["question_id"].endswith("_abs"):
                    yield e
    except ImportError:
        for e in json.loads(path.read_text()):
            if not e["question_id"].endswith("_abs"):
                yield e


def _search(ao, q: str, mode: str):
    if mode == "hybrid":
        return ao.search(q, limit=10)
    if mode == "semantic":
        return ao.search(q, limit=10, hybrid=False)
    return ao.keyword.search(q, 10)


def run(args) -> None:
    logging.disable(logging.WARNING)
    from ompa import Ompa

    out = Path(args.out)
    done = set()
    if out.exists():
        done = {json.loads(line)["qid"] for line in out.open() if line.strip()}
    with out.open("a") as fo:
        for i, e in enumerate(_iter_questions(Path(args.data))):
            if i % args.nshards != args.shard or e["question_id"] in done:
                continue
            if args.limit is not None and i >= args.limit:
                break
            sids = e["haystack_session_ids"]
            safe = [re.sub(r"[^\w-]", "_", s) for s in sids]
            back = dict(zip(safe, sids, strict=True))
            rec = {
                "qid": e["question_id"],
                "qtype": e["question_type"],
                "n_sessions": len(sids),
                "answers": sorted(e["answer_session_ids"]),
            }

            with tempfile.TemporaryDirectory() as tmp:
                ao = Ompa(vault_path=tmp, enable_semantic=True)
                for s, sess in zip(safe, e["haystack_sessions"], strict=True):
                    text = "\n".join(f"{t['role']}: {t['content']}" for t in sess)
                    ao.write(content=text, file_path=f"work/sessions/{s}.md", tags=["lme"])
                t0 = time.perf_counter()
                ao.rebuild_index()
                rec["index_s"] = time.perf_counter() - t0
                q = e["question"]
                ao.search(q, limit=10)  # warm-up (first refresh)
                for mode in MODES:
                    t0 = time.perf_counter()
                    results = _search(ao, q, mode)
                    rec[mode + "_ms"] = (time.perf_counter() - t0) * 1000
                    rec[mode] = [back.get(Path(r.path).stem, Path(r.path).stem) for r in results]
            fo.write(json.dumps(rec) + "\n")
            fo.flush()
            print(f"{rec['qid']}: index {rec['index_s']:.1f}s", flush=True)


def _metrics(ranked: list[str], answers: list[str]) -> dict[str, float]:
    ans = set(answers)
    dcg = sum(1 / math.log2(i + 2) for i, d in enumerate(ranked[:10]) if d in ans)
    idcg = sum(1 / math.log2(i + 2) for i in range(min(len(ans), 10)))
    return {
        "recall_any@1": float(bool(set(ranked[:1]) & ans)),
        "recall_any@5": float(bool(set(ranked[:5]) & ans)),
        "recall_all@5": float(ans <= set(ranked[:5])),
        "recall_any@10": float(bool(set(ranked[:10]) & ans)),
        "ndcg@10": dcg / idcg if idcg else 0.0,
    }


def _ci(x):
    import numpy as np

    x = np.asarray(x, dtype=float)
    rng = np.random.default_rng(SEED)
    bm = x[rng.integers(0, len(x), (B, len(x)))].mean(1)
    return round(float(x.mean()), 4), [
        round(float(np.percentile(bm, 2.5)), 4),
        round(float(np.percentile(bm, 97.5)), 4),
    ]


def analyze(args) -> None:
    import numpy as np

    recs = []
    for pattern in args.files:
        for f in sorted(glob.glob(pattern)):
            with open(f) as fh:
                recs += [json.loads(line) for line in fh if line.strip()]
    modes = [m for m in MODES if recs and m in recs[0]]
    report = {"n_questions": len(recs), "bootstrap_B": B, "seed": SEED, "modes": {}, "paired": []}
    per = {m: [_metrics(r[m], r["answers"]) for r in recs] for m in modes}
    for m in modes:
        report["modes"][m] = {k: _ci([p[k] for p in per[m]]) for k in per[m][0]}
        lat = [r[m + "_ms"] for r in recs]
        report["modes"][m]["search_ms_p50_p95"] = [
            round(float(np.percentile(lat, 50)), 2),
            round(float(np.percentile(lat, 95)), 2),
        ]
    for a in modes:
        for b in modes:
            if a < b:
                d = [
                    x["recall_any@5"] - y["recall_any@5"]
                    for x, y in zip(per[a], per[b], strict=True)
                ]
                report["paired"].append({"a": a, "b": b, "recall_any@5_diff": _ci(d)})
    idx = [r["index_s"] for r in recs]
    report["rebuild_index_s_p50_p95"] = [
        round(float(np.percentile(idx, 50)), 2),
        round(float(np.percentile(idx, 95)), 2),
    ]
    print(json.dumps(report, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--data", required=True)
    r.add_argument("--out", required=True)
    r.add_argument("--shard", type=int, default=0)
    r.add_argument("--nshards", type=int, default=1)
    r.add_argument("--limit", type=int, default=None, help="only the first N questions")
    a = sub.add_parser("analyze")
    a.add_argument("files", nargs="+")
    args = ap.parse_args()
    run(args) if args.cmd == "run" else analyze(args)


if __name__ == "__main__":
    main()
