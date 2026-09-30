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
  <img src="https://img.shields.io/badge/code%20style-ruff-purple" alt="Ruff">
  <img src="https://img.shields.io/badge/MCP-15%20tools-green" alt="MCP Tools">
</p>

---

> **Obsidian-MemPalace-Agnostic** — Give any AI agent persistent memory in one `pip install`.
> Works with Claude Code, OpenClaw, Codex, Gemini CLI, LangChain, or any custom agent.

```bash
pip install ompa
ao init && ao session-start
```

**96.6% R@5 on LongMemEval** using verbatim storage — no summarization loss, no API cost for search.

---

## Why OMPA?

Every AI agent starts empty every session. Important decisions get lost. Context grows expensive. Summaries lose nuance.

OMPA solves all three:

| Problem | OMPA's Answer |
|---|---|
| Lost decisions | Vault — every significant event persisted as markdown |
| Expensive context | 5 lifecycle hooks with token budgets (~2K at start, ~100 per message) |
| Summarization loss | Verbatim storage — proven 96.6% R@5 on LongMemEval |
| Framework lock-in | Works with any Python agent, any LLM |
| API cost for search | Local `sentence-transformers` — zero per-query cost |

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
ao search "authentication decisions"          # Local semantic search, zero API cost
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

# Semantic search
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

### MCP Server (15 Tools)

Plug directly into **Claude Desktop, Cursor, or Windsurf** with one command:

```bash
claude mcp add ompa -- python -m ompa.mcp_server
```

| Tool | Description |
|------|-------------|
| `ao_session_start` | Inject full memory context (~2K tokens) |
| `ao_classify` | Route a message to the right vault folder |
| `ao_search` | Semantic search across vault |
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
| `ao_stop` | Clean session shutdown |

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
ao search <query>    Semantic search
ao orphans           Detect orphaned notes
ao wrap-up           Session summary and save
ao wings             List palace wings
ao rooms <wing>      List rooms in a wing
ao tunnel            Create/traverse cross-wing tunnel
ao kg-query <entity> Query knowledge graph
ao kg-timeline <e>   Entity timeline
ao kg-stats          Knowledge graph statistics
ao validate          Validate vault structure
ao rebuild-index     Rebuild the semantic index
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
| Semantic search | **Local (free)** | ChromaDB API | QMD (paid) |
| Temporal KG | **SQLite ✓** | SQLite ✓ | ✗ |
| MCP server | **15 tools** | 15 tools | ✗ |
| CLI | **14 commands** | ✗ | ✗ |
| Lifecycle hooks | **5** | 3 | 3 |
| Message types | **15** | 15 | 5 |
| Verbatim storage | **✓** | ✓ | ✗ |
| Multi-agent | **✓** | ✗ | ✗ |
| Dual-vault isolation | **✓** | ✗ | ✗ |

---

## Installation Options

```bash
# Core (vault + palace + KG + CLI + MCP server)
pip install ompa

# With local semantic search (adds sentence-transformers + numpy)
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
├── semantic.py          # Local semantic search (lazy model loading)
├── mcp_server.py        # MCP protocol server (15 tools)
├── config.py            # Dual-vault configuration
└── cli.py               # typer CLI (14 commands)
```

---

## Credits & Attribution

OMPA is a synthesis of ideas from the AI agent memory community:

- **[MemPalace](https://github.com/corbt/mem_palace)** by Kyle Corbitt — palace metaphor (wings/rooms/drawers), temporal KG design, and verbatim storage approach (96.6% R@5 on LongMemEval)
- **[obsidian-mind](https://github.com/obsidian-ai/obsidian-mind)** — vault structure (brain/work/org/perf), wikilink conventions, frontmatter validation, session lifecycle patterns
- **Claude Code / Anthropic** — hook patterns and agent-tool interaction models
- **OpenClaw** — framework-agnostic agent runtime that inspired the "universal" design goal

---

## License

MIT — [Micap AI](https://micap.ai)
