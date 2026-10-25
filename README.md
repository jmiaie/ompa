<p align="center">
  <h1 align="center">OMPA</h1>
  <p align="center"><strong>Universal AI Agent Memory Layer</strong></p>
  <p align="center">Vault · Palace · Temporal Knowledge Graph</p>
</p>

<p align="center">
  <a href="https://github.com/jmiaie/ompa/actions/workflows/ci.yml"><img src="https://github.com/jmiaie/ompa/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/ompa/"><img src="https://img.shields.io/pypi/v/ompa?color=blue" alt="PyPI"></a>
  <a href="https://pypi.org/project/ompa/"><img src="https://img.shields.io/pypi/dm/ompa?color=blue" alt="Downloads"></a>
  <a href="https://pypi.org/project/ompa/"><img src="https://img.shields.io/pypi/pyversions/ompa" alt="Python"></a>
  <a href="https://github.com/jmiaie/ompa/blob/main/LICENSE"><img src="https://img.shields.io/pypi/l/ompa" alt="License"></a>
  <a href="https://codecov.io/gh/jmiaie/ompa"><img src="https://codecov.io/gh/jmiaie/ompa/branch/main/graph/badge.svg" alt="Coverage"></a>
  <a href="https://jmiaie.github.io/ompa/"><img src="https://img.shields.io/badge/docs-jmiaie.github.io%2Fompa-blue" alt="Docs"></a>
  <img src="https://img.shields.io/badge/code%20style-ruff-purple" alt="Ruff">
</p>

---

> **Obsidian-MemPalace-Agnostic** — Give any AI agent persistent memory in one `pip install`.
> Works with Claude Code, OpenClaw, Codex, Gemini CLI, LangChain, or any custom agent.

```bash
pip install ompa
ao init && ao session-start
```

Verbatim storage plus local hybrid search (BM25 + embeddings), with no API cost for search. Measured on
the full LongMemEval-S (n=470): **R@5 0.979** [95% CI 0.964–0.991] for hybrid search and **0.970** with
keyword-only search, which needs no ML dependencies. Method, machine and reproduction steps are in
[`benchmarks/README.md`](benchmarks/README.md).

### Benchmarks at a glance

| What | Result | Data / n |
|---|---|---|
| LongMemEval-S R@5 (session retrieval) | hybrid **0.979** [0.964, 0.991]; keyword-only **0.970** [0.953, 0.985] | 470 questions |
| Search latency, hybrid p50 (vs 1.0.8) | 1k: 17.4 → **2.1 ms** · 10k: 185 → **9.4 ms** · 50k: 1,061 → **44 ms** (8–24× faster) | synthetic notes, stub embedder |
| Temporal KG point-in-time accuracy (vs 1.0.8) | 0.895 → **1.000** | 20-seed Monte Carlo, 400 queries/seed |
| Audit log tamper detection | unsigned **24,998 / 24,998**, HMAC **25,000 / 25,000**, 0 false positives | randomized tamper Monte Carlo, 5 seeds |
| Full-chain rewrite (no key) | detected **only** against an external anchor (1,500 / 1,500 with, 0 / 1,500 without) | by design; see [docs/AUDIT.md](docs/AUDIT.md) |
| Audit write overhead | +0.50 ms p50 per write (unsigned), +0.84 ms (Ed25519) | 900 writes/mode |
| Head-to-head vs LLM-memory systems (LME-S session retrieval, same questions; LLM `gpt-4o-mini`, MiniLM embeddings) | OMPA R@5 **1.000**, MRR **0.967**; Locus 1.000 / 0.917; mem0 2.2.1 0.933 / 0.722 (ΔMRR vs OMPA −0.24, sig.); LangMem 0.767 / 0.606 (−0.36, sig.). OMPA uses no LLM at ingest or query; mem0/LangMem spend ~4 min and 4–6¢ of LLM per 48-session haystack. Zep (Graphiti OSS) and Letta: **in progress, partial n** | 30-question stratified subset (low power) |

Latencies come from a shared VM and are indicative. Head-to-head method, per-question results and harness:
[locus `benchmarks/published/2026-10-24-headtohead/`](https://github.com/jmiaie/locus/tree/main/benchmarks/published/2026-10-24-headtohead)
(OMPA main `34d533e`, hybrid search with MiniLM).

---

## Why OMPA?

Every AI agent starts empty every session. Important decisions get lost. Context grows expensive. Summaries lose nuance.

OMPA solves all three:

| Problem | OMPA's Answer |
|---|---|
| Lost decisions | Vault — every significant event persisted as markdown |
| Expensive context | 5 lifecycle hooks with token budgets (~2K at start, ~100 per message) |
| Summarization loss | Verbatim storage; search runs over the full note text ([measured](benchmarks/README.md)) |
| Framework lock-in | Works with any Python agent, any LLM |
| API cost for search | Local SQLite FTS5 (BM25) out of the box; optional local `sentence-transformers` for hybrid search |

---

## Companion stack

OMPA is the **memory + temporal KG** layer. For retrieval and compliance, pair it with:

| Layer | Project | Role |
|---|---|---|
| Memory schema | **OMPA** ([PyPI](https://pypi.org/project/ompa/)) | Vault · palace · temporal KG · MCP · lifecycle hooks |
| Explainable retrieval | **[Locus](https://github.com/jmiaie/locus)** ([`locus-rag`](https://pypi.org/project/locus-rag/)) | Vectorless RAG — BM25 + KG + link walking, zero GPU |
| Compliance product | **[CognitionOS](https://github.com/jmiaie/cognition-os)** | Audit · RBAC · retention · PHI redaction on top of Locus + OMPA |

**Consulting one-pager:** [Why OMPA vs raw Obsidian?](docs/why-ompa.md)

---

## Three-Layer Architecture

```
┌───────────────────────────────────────────────────────────────┐
│  Layer 1: Vault  (human-navigable markdown)                   │
│  brain/  work/  org/  perf/  ← obsidian-mind structure        │
├───────────────────────────────────────────────────────────────┤
│  Layer 2: Palace  (agent-accessible metadata)                 │
│  wings → rooms → drawers  (vault file references)             │
│  halls: facts · events · discoveries · preferences            │
│  tunnels: cross-wing connections                              │
├───────────────────────────────────────────────────────────────┤
│  Layer 3: Knowledge Graph  (temporal triples)                 │
│  SQLite: subject → predicate → object + validity window       │
│  Query any entity's history at any point in time              │
└───────────────────────────────────────────────────────────────┘
```

---

## Quick Start

### 1. Install

```bash
pip install ompa          # Core only
pip install ompa[all]     # Includes local semantic search
```

### 2. Initialize a vault

```bash
ao init              # Create vault structure
ao status            # Verify everything looks good
```

### 3. Use in a session

```bash
ao session-start                              # ~2K token context injection
ao classify "We decided to go with Postgres"  # Routes to right folder automatically
ao search "authentication decisions"          # Local hybrid search (keyword-only without ompa[semantic])
ao kg-query Kai                               # Query the knowledge graph
ao wrap-up                                    # Session summary + save to vault
```

### 4. Python API

```python
from ompa import Ompa

ao = Ompa(vault_path="./workspace")

# Lifecycle
context = ao.session_start()              # Returns ~2K token context string
hint = ao.handle_message("We won the enterprise deal!")
ao.post_tool("write", {"file_path": "work/active/auth.md"})
ao.stop()

# Search (hybrid BM25 + semantic; BM25 only without ompa[semantic])
results = ao.search("authentication decisions", wing="Orion")

# Knowledge graph
ao.kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
triples = ao.kg.query_entity("Kai")
timeline = ao.kg.timeline("Orion")

# Palace navigation
ao.palace.create_wing("Orion", type="project")
ao.palace.create_tunnel("Kai", "Orion", "auth-migration")
```

---

## Features

### 5 Lifecycle Hooks

| Hook | Token Budget | Fires When |
|------|-------------|------------|
| `session_start` | ~2,000 | Session begins — full context injection |
| `user_message` | ~100 | Each incoming user message |
| `post_tool` | ~200 | After each tool call |
| `pre_compact` | ~100 | Before context compaction |
| `stop` | ~500 | Session ends — wrap-up and persist |

### 15 Message Types

Auto-classified and routed to the right vault folder:

`DECISION` · `INCIDENT` · `WIN` · `LOSS` · `BLOCKER` · `QUESTION` · `SUGGESTION` · `REVIEW` · `BUG` · `FEATURE` · `LEARN` · `RETROSPECTIVE` · `ALERT` · `STATUS` · `CHORE`

```bash
ao classify "We decided to go with Postgres over MySQL"
# → MessageType.DECISION → vault/work/decisions/2026-05-07-postgres.md
```

### MCP Server (24 Tools)

Plug directly into **Claude Desktop, Cursor, or Windsurf** with one command:

```bash
claude mcp add ompa -- python -m ompa.mcp_server
```

| Tool | Description |
|------|-------------|
| `ao_session_start` | Inject full memory context (~2K tokens) |
| `ao_classify` | Route a message to the right vault folder |
| `ao_search` | Hybrid keyword + semantic search across vault |
| `ao_kg_query` | Query knowledge graph for an entity |
| `ao_kg_add` | Add a triple to the knowledge graph |
| `ao_kg_stats` | Knowledge graph statistics |
| `ao_palace_wings` | List all wings |
| `ao_palace_rooms` | List rooms in a wing |
| `ao_palace_tunnel` | Create/traverse cross-wing tunnel |
| `ao_validate` | Validate vault structure |
| `ao_wrap_up` | Session summary + persist |
| `ao_status` | Vault health status |
| `ao_orphans` | Detect orphaned notes |
| `ao_init` | Initialize a new vault |
| `ao_write` | Write a note (indexed, KG-extracted, audited) |
| `ao_kg_populate` | Extract facts from notes into the KG |
| `ao_sync` | Sync the vault (git / rsync / S3) |
| `ao_export` / `ao_import` | Move notes between personal and shared vaults |
| `ao_audit_verify` | Verify the tamper-evident audit chain (+ note drift) |
| `ao_audit_head` | Current chain head, for external anchoring |
| `ao_audit_export` | Exportable JSON evidence bundle |
| `ao_forget` | Right-to-erasure (dry run by default) |
| `ao_as_of` | What the agent knew at time T |

### Audit Memory (opt-in)

Tamper-evident, hash-chained audit log of every memory mutation, with Merkle
checkpoints, optional Ed25519/HMAC signing and exportable evidence bundles.
Also: citations (path, line/byte span, content hash, author, recorded and valid
time) on search and KG results, `as_of(t)` point-in-time views, retention TTLs,
legal hold, right-to-erasure with proof of deletion, and per-vault ACLs.
Stdlib-only (`pip install ompa[audit]` adds Ed25519). Off by default.

```python
ao = Ompa(vault_path="./vault", audit=True, principal="agent-7")
ao.search("Orion budget")[0].citation
ao.audit_verify()["ok"]
```

See [docs/AUDIT.md](docs/AUDIT.md) for the threat model, the guarantees and
non-guarantees, migration and a compliance mapping (supports, not certifies).

### Dual-Vault Mode

Isolate team/org content from personal or private notes:

```python
from ompa import Ompa, DualVaultConfig, IsolationMode

config = DualVaultConfig(
    shared_vault="./team-vault",
    personal_vault="./private-vault",
    mode=IsolationMode.AUTO,
)
ao = Ompa(config=config)
```

---

## CLI Reference

```
ao init              Initialize a new vault
ao status            Health check and stats
ao session-start     Inject memory context (use at session start)
ao classify <msg>    Classify and route a message
ao search <query>    Hybrid search (BM25 + semantic)
ao orphans           Detect orphaned notes
ao wrap-up           Session summary and save
ao wings             List palace wings
ao rooms <wing>      List rooms in a wing
ao tunnel            Create/traverse cross-wing tunnel
ao kg-query <entity> Query knowledge graph
ao kg-timeline <e>   Entity timeline
ao kg-stats          Knowledge graph statistics
ao validate          Validate vault structure
ao rebuild-index     Rebuild the search indexes
ao audit …           enable | verify | head | log | export | verify-bundle | keygen | as-of
ao forget <subject>  Right-to-erasure with a tombstone in the audit chain
ao retention …       add | list | apply | remove   (TTL rules)
ao hold …            add | release                 (legal hold)
ao acl …             init | show                   (vault access control)
```

---

## Framework Compatibility

| Agent Framework | Integration Method |
|---|---|
| Claude Code | Python API + MCP server |
| OpenClaw | Python API + MCP server |
| Codex | Python API + MCP server |
| Gemini CLI | Python API + MCP server |
| LangChain | Python API |
| Custom agents | Python API |

---

## Comparison

| Feature | **OMPA** | MemPalace | obsidian-mind |
|---|---|---|---|
| Framework support | **Any** | Claude Code only | Claude Code only |
| Memory layers | **Vault + Palace + KG** | Palace + KG | Vault only |
| Search | **Local BM25 + optional local embeddings (free)** | ChromaDB API | QMD (paid) |
| Temporal KG | **SQLite ✓** | SQLite ✓ | ✗ |
| MCP server | **24 tools** | 15 tools | ✗ |
| CLI | **40 commands** | ✗ | ✗ |
| Lifecycle hooks | **5** | 3 | 3 |
| Message types | **15** | 15 | 5 |
| Verbatim storage | **✓** | ✓ | ✗ |
| Multi-agent | **✓** | ✗ | ✗ |
| Dual-vault isolation | **✓** | ✗ | ✗ |

<sub>OMPA's column is checked against this repo. The other columns come from those projects' docs when the table was written and have not been re-verified.</sub>

---

## Installation Options

```bash
# Core (vault + palace + KG + CLI + MCP server)
pip install ompa

# With local semantic + hybrid search (adds sentence-transformers + numpy).
# Keyword (BM25) search over note content works without it.
pip install ompa[all]

# Development
pip install ompa[dev]

# From source
git clone https://github.com/jmiaie/ompa && cd ompa
pip install -e ".[all]"
```

Requires Python 3.10+.

---

## Package Structure

```
ompa/
├── core.py              # Ompa main class — lifecycle, hooks, dual-vault
├── vault.py             # Vault management (brain/work/org/perf)
├── palace.py            # Palace metadata (wings/rooms/drawers/halls/tunnels)
├── knowledge_graph.py   # Temporal KG (SQLite triples + validity windows)
├── hooks.py             # 5 lifecycle hooks + HookManager
├── classifier.py        # 15 message types with auto-routing
├── semantic.py          # Semantic index (resident float16 matrix) + RRF hybrid
├── keyword.py           # BM25 keyword index (stdlib SQLite FTS5)
├── audit.py             # Hash-chained audit log, Merkle checkpoints, signing, bundles
├── governance.py        # ACL, retention, legal hold, forget(), as_of views
├── provenance.py        # Citations for search / KG results
├── mcp_server.py        # MCP protocol server (24 tools)
├── config.py            # Dual-vault configuration
└── cli.py               # typer CLI (40 commands)
```

---

## Credits & Attribution

OMPA is a synthesis of ideas from the AI agent memory community:

- **[MemPalace](https://github.com/corbt/mem_palace)** by Kyle Corbitt — palace metaphor (wings/rooms/drawers), temporal KG design, and verbatim storage approach
- **[obsidian-mind](https://github.com/obsidian-ai/obsidian-mind)** — vault structure (brain/work/org/perf), wikilink conventions, frontmatter validation, session lifecycle patterns
- **Claude Code / Anthropic** — hook patterns and agent-tool interaction models
- **OpenClaw** — framework-agnostic agent runtime that inspired the "universal" design goal

---

## License

MIT — [Micap AI](https://micap.ai)
