"""
Search latency / index size / cold start at 1k, 10k, 50k notes (synthetic).

Each note is one chunk of ~83 Zipf-distributed synthetic words plus two words
unique to it.  A deterministic stub embedder (384-d Gaussian seeded by the
text's crc32) stands in for the model, so the numbers isolate OMPA's own
index and search code; add the model's query-encode time (~5-15 ms for
all-MiniLM-L6-v2 on CPU) for real-world semantic latency.

Queries are 3 common words + the target note's 2 unique words, so
``content_hit@5`` checks that a note is findable by its content.

Usage:
    pip install numpy            # or ompa[semantic]
    python benchmarks/search_latency.py              # 1k, 10k, 50k
    python benchmarks/search_latency.py --sizes 1000 --queries 30
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import tempfile
import time
import zlib
from pathlib import Path

import numpy as np

from ompa import Ompa
from ompa.keyword import KeywordIndex
from ompa.semantic import SemanticIndex

SEED = 20261020
VOCAB = [f"w{i:04d}" for i in range(3000)]


class StubEmbedder:
    def encode(self, text: str):
        rng = np.random.default_rng(zlib.crc32(text.encode()))
        return rng.standard_normal(384).astype(np.float32)


def note_words(i: int) -> list[str]:
    r = np.random.default_rng(SEED + i)
    words = [VOCAB[j] for j in r.zipf(1.3, 80) % len(VOCAB)]
    return words + [f"topic{i % 97}", f"uniq{i:06d}a", f"uniq{i:06d}b"]


def pct(x) -> dict:
    return {
        "p50_ms": round(float(np.percentile(x, 50)), 3),
        "p95_ms": round(float(np.percentile(x, 95)), 3),
    }


def bench(n: int, n_queries: int) -> dict:
    work = Path(tempfile.mkdtemp(prefix="ompa_bench_"))
    try:
        vault = work / "vault"
        (vault / "notes").mkdir(parents=True)
        for i in range(n):
            (vault / "notes" / f"n{i:06d}.md").write_text(" ".join(note_words(i)))
        palace = vault / ".palace"
        kw = KeywordIndex(palace / "keyword_index.sqlite3")
        idx = SemanticIndex(
            palace / "semantic_index", embedding_backend=StubEmbedder(), keyword_index=kw
        )
        r: dict = {"notes": n}
        t0 = time.perf_counter()
        idx.index_vault(vault)
        r["build_semantic_s"] = round(time.perf_counter() - t0, 3)
        t0 = time.perf_counter()
        kw.refresh(vault)
        r["build_keyword_s"] = round(time.perf_counter() - t0, 3)

        rng = np.random.default_rng(SEED + 7)
        targets = [int(t) for t in rng.integers(0, n, n_queries)]
        queries = [
            " ".join(note_words(t)[:3] + [f"uniq{t:06d}a", f"uniq{t:06d}b"]) for t in targets
        ]

        def timed(fn):
            lat, hits = [], 0
            for q, t in zip(queries, targets, strict=True):
                a = time.perf_counter()
                res = fn(q)
                lat.append((time.perf_counter() - a) * 1000)
                hits += any(Path(x.path).stem == f"n{t:06d}" for x in res)
            return {**pct(lat), "content_hit@5": hits / len(queries)}

        idx.search(queries[0], 5)  # warm-up
        r["hybrid"] = timed(lambda q: idx.search(q, 5))
        r["semantic_only"] = timed(lambda q: idx.search(q, 5, hybrid=False))
        r["keyword_only"] = timed(lambda q: kw.search(q, 5))

        t0 = time.perf_counter()
        idx.save_index()
        r["save_s"] = round(time.perf_counter() - t0, 3)
        files = list((palace / "semantic_index").iterdir())
        r["semantic_index_mb"] = round(sum(f.stat().st_size for f in files) / 2**20, 2)
        kw.close()
        r["keyword_index_mb"] = round(
            sum(f.stat().st_size for f in palace.glob("keyword_index.sqlite3*")) / 2**20, 2
        )
        cold = SemanticIndex(palace / "semantic_index", embedding_backend=StubEmbedder())
        t0 = time.perf_counter()
        cold.load_index()
        cold.search(queries[1], 5)
        r["cold_load_plus_first_query_s"] = round(time.perf_counter() - t0, 3)

        ao = Ompa(vault_path=str(vault), enable_semantic=False)
        ao.search("warm up", 5)
        r["ompa_search_without_semantic_extra"] = timed(lambda q: ao.search(q, 5))
        return r
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    logging.disable(logging.WARNING)
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--sizes", type=int, nargs="+", default=[1000, 10000, 50000])
    ap.add_argument("--queries", type=int, default=30)
    args = ap.parse_args()
    for n in args.sizes:
        print(json.dumps(bench(n, args.queries)), flush=True)


if __name__ == "__main__":
    main()
