# Date-aware ranking (`search(temporal=True)`) on LongMemEval-S, n=470 (2026-11-12)

Re-measurement of the implemented opt-in feature (not the earlier prototype). OMPA hybrid search (MiniLM + keyword),
`search(limit=10)`; every session note is written with `write(date=<session date>)`; `reference_date` = the question
date. Paired bootstrap vs the dated baseline, B=10,000, seed 20261020. No LLM, $0.

| | MRR@10 | ΔMRR [95% CI] | ΔR_all@5 |
|---|---|---|---|
| baseline (dated notes) | 0.9323 | — | — |
| `temporal=True` | 0.9351 | +0.0028 [−0.0063, +0.0121], **not significant** | −0.0064, not significant |

Per type, the only significant change is **temporal-reasoning (n=127): 0.893 → 0.918, +0.025 [+0.002, +0.053]**.
Multi-session −0.008 and single-session-user −0.014 are not significant. 22 of 470 questions changed. Search latency
p50 28.5 ms with or without the feature. The prototype measured +0.0039 overall (not significant) and +0.029 on
temporal-reasoning (significant); same top-1 session as the prototype on 464/470 questions.

Baseline note: writing the session date into the frontmatter changes OMPA's index slightly, so this baseline matches the
original LME-500 run's top 10 on 314/470 questions (MRR 0.9323 vs 0.9325).

Caveats: one dataset; the parser was narrowed post hoc (duration ranges such as "past few months" and windows wider than
45 days are ignored), and this narrowing did not remove the small non-significant losses. No general recency prior
(the prototype measured one as strongly harmful).

Harness, per-question rows and the prototype outputs:
[jmiaie/locus `benchmarks/published/2026-11-12-ranking/`](https://github.com/jmiaie/locus/tree/main/benchmarks/published/2026-11-12-ranking)
(`remeasure.py`, `rows__ompa.jsonl`). `summary.json` here is the same file as `summary__ompa.json` there.
