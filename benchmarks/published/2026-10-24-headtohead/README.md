# Head-to-head on 30 LongMemEval-S questions (copy)

Copies of the files the README charts are drawn from. The canonical tree, with the harness, the full README and the
Gemini rows, is [jmiaie/locus `benchmarks/published/2026-10-24-headtohead/`](https://github.com/jmiaie/locus/tree/main/benchmarks/published/2026-10-24-headtohead).

- `summary.json`: per-system metrics, cost and call counts (gpt-4o-mini via OpenRouter, gateway logs including pilot
  runs, so cost and calls are upper bounds), paired bootstrap vs Locus and OMPA.
- `results/lme30__*.jsonl`: per-question top-10 rows for Locus, OMPA, mem0 2.2.1, LangMem 0.0.30, Graphiti 0.31.2 and
  Letta 0.16.8.
- `scripts/lme_subset30.json`: the 30 question ids (5 per question type).

n=30 is low power. Open-source libraries with default settings, not the vendors' managed services.
