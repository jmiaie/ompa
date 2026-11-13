# Competitors' own published numbers (collected 2026-11-12; cited, not re-run)

Every number below is copied from the linked source. None was measured by us. Almost all are **end-to-end QA accuracy** (retrieve → reader LLM answers → judge LLM grades), so they are **not directly comparable** to our retrieval metrics (session-level R@5 / MRR, no reader, no judge).

| System | Benchmark | Score | Metric | Setup (as stated by the source) | Source |
|---|---|---|---|---|---|
| mem0 (managed platform, "new algorithm", Apr 2026) | LongMemEval (500 q; S variant not stated on the page) | 94.4% (top_200), 94.8% (top_50) | LLM-judge QA accuracy | Single-pass retrieval (1 retrieval, 1 answer); platform includes proprietary optimizations not in OSS; ~6,787 retrieval tokens/query; ±1 pt judge noise. Repo defaults: answerer gpt-4o, judge gpt-4o, extraction gpt-4o-mini, text-embedding-3-small. Per type (top_200): KU 93.6, MS 88.0, SSA 98.2, SSP 96.7, SSU 98.6, TR 97.0 | https://github.com/mem0ai/memory-benchmarks ; https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm ; https://docs.mem0.ai/core-concepts/memory-evaluation |
| mem0 (old algorithm, per mem0) | LongMemEval | 67.8% | LLM-judge QA accuracy | as above, previous algorithm | https://mem0.ai/blog/mem0-the-token-efficient-memory-algorithm |
| mem0 OSS (self-hosted) | LongMemEval | 88.6–91.0% depending on extraction LLM (GPT-5: 91.0%) | LLM-judge QA accuracy | Qwen 600M embedder, Qdrant, GPT-5 answerer + judge | https://github.com/mem0ai/memory-benchmarks |
| mem0 (managed platform) | LoCoMo (1,540 q) | 92.5% (top_200) | LLM-judge QA accuracy | single-pass; ~6,956 tokens/query | same as above |
| mem0 / mem0-graph (2025 paper) | LoCoMo | 66.88% / 68.44%; search p50/p95 0.148/0.200 s (mem0) | LLM-judge (J) | gpt-4o-mini, text-embedding-3-small | https://arxiv.org/abs/2504.19413 (Table 2) |
| Zep (cloud, 2026) | LongMemEval (500 q) | 90.2% (451/500); retrieval p50/p95 104/162 ms; median context 4,408 tokens | LLM-judge QA accuracy | Reader gpt-5.4 (reasoning medium), judge gpt-5.4 with CoT grading; multi-scope retrieval + cross-encoder rerank. Per type: SSA 96.4, SSU 94.3, KU 93.6, TR 90.2, SSP 90.0, MS 83.5 | https://www.getzep.com/research/memory-benchmarks/ |
| Zep (cloud, 2026) | LoCoMo (1,540 q) | 94.7%; retrieval p50/p95 87/155 ms | LLM-judge QA accuracy | same as above | same |
| Zep (paper, Jan 2025) | LongMemEval_S | 63.8% (gpt-4o-mini reader), 71.2% (gpt-4o reader); full-context baselines 55.4% / 60.2% | LLM-judge QA accuracy (GPT-4o judge, LongMemEval prompts) | gpt-4o-mini for graph construction, BGE-m3 embed + rerank; Zep cloud | https://arxiv.org/abs/2501.13956 (Table 2) |
| Zep (Zep blog rebuttal, 2025) | LoCoMo | 75.14% ± 0.17 | LLM-judge (J) | Zep's corrected run of mem0's harness | https://www.getzep.com/blog/lies-damn-lies-statistics-is-mem0-really-sota-in-agent-memory/ |
| Graphiti OSS (self-hosted, what we ran) | — | no first-party OSS-only numbers found; Zep numbers above are for the Zep cloud service built on Graphiti | — | — | — |
| LangMem | LongMemEval / LoCoMo | **no first-party (LangChain) benchmark found.** Third-party (mem0 paper): LoCoMo J 58.10%, search p50/p95 17.99/59.82 s | LLM-judge (J) | gpt-4o-mini, text-embedding-3-small, run by mem0 | https://arxiv.org/abs/2504.19413 (Table 2) |
| Letta | LoCoMo | 74.0% | LLM-judge QA accuracy | Letta Filesystem agent (grep/search_files/open/close), gpt-4o-mini, conversation stored as files (not Letta archival memory) | https://www.letta.com/blog/benchmarking-ai-agent-memory/ |
| Letta | LongMemEval | **no first-party number found** | — | — | — |
| (reference) LongMemEval paper | LongMemEval_M retrieval, value=session | Recall@5 0.634 (BM25), 0.720 (Stella V5 1.5B), 0.723 (Contriever) | strict retrieval recall (paper calls it strict; ~500 sessions/haystack) | flat index K=V | https://arxiv.org/abs/2410.10813 (Table 9) |

Notes:
- Vendors use different reader/judge LLMs (gpt-4o, gpt-5, gpt-5.4), different retrieval depths (top-50/200), and in mem0's and Zep's case their managed cloud services, so even vendor-to-vendor numbers are not apples-to-apples. Zep's page also reports a mem0 LongMemEval number (90.4% top_50) that differs from mem0's own (94.8%); we cite each vendor only for its own system.
- The LongMemEval paper's retrieval numbers are on LongMemEval_M (≈500 sessions per haystack; Table 9's round-level Stella K=V+fact row equals Table 3, which is captioned LongMemEval_M), not _S (≈48), and keys keep only user-side utterances. So they are the right *metric family* but a 10× larger haystack than ours; not directly comparable.
