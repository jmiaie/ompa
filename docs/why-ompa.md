# Why OMPA vs raw Obsidian?

**One page for consulting leads.** OMPA is the installable agent-memory layer that turns an Obsidian-style markdown vault into durable, queryable memory for any AI agent — without lock-in to a single IDE or paid embedding API.

---

## The consulting problem

Teams already dump decisions into Obsidian (or a shared markdown vault). That works for humans. It fails for agents:

| Pain | What raw Obsidian gives you | What agents need |
|---|---|---|
| Lost decisions across sessions | Notes in folders | Lifecycle hooks that inject ~2K tokens of relevant memory at session start |
| Expensive context windows | Manual copy/paste | Token-budgeted hooks (~100–200 per turn) |
| “Search” that bleeds money | Embeddings + vector DB | Local semantic search (or [Locus](https://github.com/jmiaie/locus) vectorless RAG) |
| No provenance | Wikilinks humans follow | Temporal knowledge graph + explainable retrieval |
| Framework lock-in | Plugins for one app | Python API + MCP — Claude Code, Cursor, LangChain, custom agents |

OMPA is the productized answer: **`pip install ompa`** → vault + palace + temporal KG in one package.

---

## What you get in one install

```
Vault (human markdown)  →  Palace (agent metadata)  →  Temporal KG (SQLite facts)
```

- **Vault** — brain / work / org / perf layout; every significant event as markdown humans can audit
- **Palace** — wings → rooms → drawers, halls, tunnels — agent-navigable structure over the vault
- **Knowledge graph** — subject → predicate → object with validity windows; query “what was true when?”
- **15 MCP tools + CLI (`ao`)** — drop into Claude Desktop, Cursor, Windsurf, or any MCP host
- **Dual-vault isolation** — shared team vault vs personal notes without leaking identifiers

Benchmark signal: **96.6% R@5 on LongMemEval** with verbatim storage (no summarization loss, no per-query API cost for local search).

```bash
pip install ompa
ao init && ao session-start
```

---

## Why not “just Obsidian”?

Raw Obsidian is an excellent **human** second brain. It is not an agent memory runtime:

1. **No session contract** — agents do not get a budgeted `session_start` / `wrap-up` lifecycle
2. **No typed routing** — OMPA auto-classifies 15 message types (DECISION, INCIDENT, WIN, …) into the right folders
3. **No temporal facts** — Obsidian links are spatial; OMPA’s KG tracks when a fact was valid
4. **No MCP surface** — consultants can wire memory into client agents in minutes, not weeks of plugin glue
5. **No dual-vault hygiene** — team vs personal isolation is a first-class config, not a folder convention

Keep Obsidian (or any markdown editor) for humans. Put **OMPA underneath** so agents write and recall the same corpus safely.

---

## Companion stack (retrieval + compliance)

OMPA is the **memory schema and temporal KG**. Pair it with:

| Piece | Repo | Role |
|---|---|---|
| **OMPA** | [jmiaie/ompa](https://github.com/jmiaie/ompa) · [PyPI](https://pypi.org/project/ompa/) | Vault + palace + temporal KG; agent lifecycle + MCP |
| **Locus** | [jmiaie/locus](https://github.com/jmiaie/locus) · [PyPI `locus-rag`](https://pypi.org/project/locus-rag/) | Vectorless RAG (BM25 + KG + link walking); explainable retrieval, zero GPU |
| **CognitionOS** | [jmiaie/cognition-os](https://github.com/jmiaie/cognition-os) | Compliance-grade composition — audit, RBAC, retention, PHI redaction on top of Locus + OMPA |

**Pitch in one sentence:** install OMPA for agent memory; add Locus when you need defensible “why this result”; bring CognitionOS when regulated clients need audit trails.

```
Agents / MCP hosts
        │
   OMPA (write + temporal memory)
        │
   Locus (explainable retrieve)  ── optional
        │
   CognitionOS (compliance SaaS) ── optional / managed
```

---

## Fit for a consulting engagement

| Stage | Deliverable |
|---|---|
| Day 0 | `pip install ompa` on the client vault; `ao init`; MCP into their agent host |
| Week 1 | Lifecycle hooks in their agent; dual-vault if personal notes must stay private |
| Scale | Locus for vectorless RAG over the same vault; CognitionOS when audit/RBAC is required |

**Open source wedge → managed upsell:** OMPA (and Locus) stay MIT/public; CognitionOS is the compliance productization path.

---

## Next step

- Docs site: [jmiaie.github.io/ompa](https://jmiaie.github.io/ompa)
- Quickstart: [quickstart.md](quickstart.md)
- Issues / support: [github.com/jmiaie/ompa](https://github.com/jmiaie/ompa)

MIT — [Micap AI](https://micap.ai)
