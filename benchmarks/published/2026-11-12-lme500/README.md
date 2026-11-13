# LongMemEval-S, all 500 questions: OMPA and Locus (2026-11-12)

Session-level retrieval on `longmemeval_s_cleaned.json`, primary set = the 470 non-abstention questions. No LLM
calls, $0. Local `all-MiniLM-L6-v2` embeddings, CPU only, shared VM. Percentile bootstrap, B=10,000, seed 20261020.

| System (470 q) | R@5 [95% CI] | R_all@5 | MRR@10 [95% CI] | search p50 / p95 | ingest p50 per history |
|---|---|---|---|---|---|
| OMPA (hybrid, `search(limit=10)`) | 0.981 [0.968, 0.992] | 0.877 | 0.933 [0.913, 0.950] | 26.0 / 57.7 ms | 10.1 s |
| Locus (FTS5 BM25) | 0.977 [0.962, 0.989] | 0.860 | 0.911 [0.889, 0.931] | 2.3 / 3.9 ms | 0.22 s |

- `summary500__locus_ompa.json`: all metrics, per-type breakdown with CIs and paired tests.
- `published_competitors.md`: vendors' own published numbers with sources (LLM-judged answer accuracy, **not
  comparable** to these retrieval metrics).
- Harness, analysis and per-question rows: [jmiaie/locus `benchmarks/published/2026-11-12-lme500/`](https://github.com/jmiaie/locus/tree/main/benchmarks/published/2026-11-12-lme500)
  and [`2026-11-12-ranking/`](https://github.com/jmiaie/locus/tree/main/benchmarks/published/2026-11-12-ranking).

This run used a slightly different harness from OMPA's own `benchmarks/README.md` run for 1.1.0 (R@5 0.979); the two
agree within their CIs.
