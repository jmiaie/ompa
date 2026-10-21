# OMPA benchmarks

Every number OMPA publishes comes from a script in this directory. Each one can be re-run.
Measured 2026-10-20 on an 8-vCPU Intel Xeon VM (16 GB RAM, Linux) with Python 3.12, numpy 2.5,
torch 2.14 (CPU), sentence-transformers 6.1 (`all-MiniLM-L6-v2`) and SQLite 3.53.
**Caveat:** the box was shared with other jobs (load average 3–13), which inflates latency tails.
Recall numbers are deterministic and unaffected by load.

| Script | What it measures |
|---|---|
| `longmemeval_s.py` | Retrieval on the full LongMemEval-S, n=470, per search mode, bootstrap 95% CIs |
| `search_latency.py` | Search p50/p95, index size, build time and cold load at 1k / 10k / 50k notes |
| `temporal_mc.py` | Temporal knowledge-graph point-in-time correctness, 20-seed Monte Carlo |
| `smoke_sample.py` | **Smoke test only:** 20 hand-written items. This is *not* LongMemEval, and its score means nothing beyond "search works" |

## 1. LongMemEval-S (session-level retrieval)

Protocol (`longmemeval_s.py`):

- Each question gets a fresh vault. Each haystack session (~50 per question, user + assistant
  turns) is written as one note, then `rebuild_index()` runs.
- The question is the query. A hit is any evidence session in the top k.
- The 30 abstention questions have no evidence session and are excluded, giving n = 470.
- CIs are 10,000 bootstrap resamples over questions, seed 20261020.

| Mode | R@5 [95% CI] | R_all@5 | nDCG@10 | R@1 | R@10 |
|---|---|---|---|---|---|
| **hybrid** (default `ao.search`; BM25 + MiniLM, RRF) | **0.979** [0.964, 0.991] | 0.874 | **0.929** | **0.891** | **0.996** |
| semantic only (`hybrid=False`) | 0.974 [0.960, 0.987] | 0.845 | 0.896 | 0.849 | 0.983 |
| keyword only (FTS5 BM25, **no ML deps**) | 0.970 [0.953, 0.985] | 0.851 | 0.910 | 0.877 | 0.989 |
| OMPA ≤ 1.0.8 default (cosine + keyword-overlap boost) | 0.981 [0.968, 0.991] | 0.874 | 0.910 | 0.851 | 0.989 |
| OMPA ≤ 1.0.8 without `ompa[semantic]` (file-name match) | n/a: matches file names only, never content | | | | |

Paired against the ≤ 1.0.8 default, same 470 questions:

- Hybrid: R@5 −0.002 [−0.015, +0.011] (not significant). nDCG@10 +0.019 [+0.008, +0.030]
  (significant: better ranking of the evidence sessions).
- Semantic only: R_all@5 −0.030 [−0.053, −0.009] (significant).

These are OMPA's own numbers. The "96.6% R@5" figure that earlier READMEs quoted is MemPalace's result
for its own pipeline. OMPA never measured it, so it was removed.

```bash
pip install "ompa[semantic]"
# longmemeval_s_cleaned.json from https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned
for s in 0 1; do python benchmarks/longmemeval_s.py run --data longmemeval_s_cleaned.json \
    --out lme_shard$s.jsonl --shard $s --nshards 2 & done; wait      # ~1 h on 8 vCPU
python benchmarks/longmemeval_s.py analyze 'lme_shard*.jsonl'
```

Each shard peaks at about 1.2 GB RSS (more if the 270 MB JSON is loaded whole; see the script
docstring). Rebuilding one ~50-session vault takes p50 9.3 s on CPU (batched embedding), versus
15.5 s in ≤ 1.0.8.

## 2. Search latency and index size (synthetic, `search_latency.py`)

Each note is one chunk of ~83 Zipf-distributed words plus 2 unique words. Queries are 3 common words
plus a target note's 2 unique words; *content hit@5* means the target note is in the top 5. A
deterministic stub embedder replaces the model, which isolates OMPA's own code. A real query adds
~5 ms of MiniLM encoding on an idle CPU. Each cell is the median of 2 rounds × 30 queries
(10 queries for 1.0.8 at 50k).

| Notes | 1.0.8 search p50 / p95 | **now: hybrid** p50 / p95 | semantic only p50 | keyword only p50 |
|---|---|---|---|---|
| 1k | 17.4 / 19.8 ms | **2.1 / 2.9 ms** | 0.12 ms | 1.1 ms |
| 10k | 185 / 236 ms | **9.4 / 13.9 ms** | 0.75 ms | 7.0 ms |
| 50k | 1,061 / 1,241 ms | **44 / 59 ms** | 3.1 ms | 32 ms |

| Notes | index on disk 1.0.8 → now (semantic + keyword) | load index | cold start, fresh process (import + load + 1 query) | peak RSS of that process |
|---|---|---|---|---|
| 1k | 8.2 MB → 1.2 + 1.0 MB | 0.09 → 0.003 s | 0.31 → 0.18 s | 81 → 51 MB |
| 10k | 82 MB → 11.9 + 10.2 MB | 0.97 → 0.03 s | 1.51 → 0.21 s | 443 → 125 MB |
| 50k | 411 MB → 59.7 + 49.5 MB | 5.2 → 0.34 s | 6.8 → 0.48 s | 2,060 → 443 MB |

Content hit@5 at 10k and 50k:

- Default search: 0.40 in 1.0.8, **1.00** now.
- Without `ompa[semantic]`: **0.00** in 1.0.8 (file names only), **1.00** now, at a p50 of 7 ms (10k)
  and 34 ms (50k).

Hybrid latency at 50k is mostly BM25 over very common query words. Semantic scoring by itself is
3 ms. A full index build at 50k takes 3.0 s (semantic, stub embedder) plus 2.0 s (keyword).

```bash
pip install numpy
python benchmarks/search_latency.py --sizes 1000 10000 50000
```

## 3. Temporal knowledge graph (`temporal_mc.py`)

Each of the 20 seeds runs 30 entities with 2–5 sequential assignments each. 35% of assignments
re-assert an earlier fact. Each seed checks 400 point-in-time queries.

| | point-in-time accuracy [95% CI] | validity intervals lost |
|---|---|---|
| OMPA ≤ 1.0.8 | 0.895 [0.886, 0.904] | 36% |
| now (since #16) | **1.000** [1.000, 1.000] | **0%** |

```bash
python benchmarks/temporal_mc.py --seeds 20
```

## Notes and limits

- LongMemEval-S tests haystacks of about 50 sessions per question. That is retrieval over small
  vaults, not a 50k-note vault. The synthetic latency benchmark covers scale but not relevance.
- Chunks are 512 words, but MiniLM reads only the first 256 tokens of each, so semantic recall on
  long notes relies on the BM25 half of hybrid search.
- The ≤ 1.0.8 LongMemEval baseline was measured on 2026-10-20 from the same harness. Its search
  code is unchanged in #16, and a fresh 40-question re-run gave identical top-5 lists.
