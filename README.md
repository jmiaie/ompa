# OMPA (OMPA)

> **Obsidian-MemPalace-Agnostic** — Universal AI agent memory layer

OMPA gives any AI agent persistent memory with vault conventions, palace navigation, and a temporal knowledge graph.

## Credits & Attribution

This project is a synthesis of ideas and code from the AI agent memory community:

- **[MemPalace](https://github.com/corbt/mem_palace)** by Kyle Corbitt — The palace metaphor (wings/rooms/drawers), temporal knowledge graph design, and verbatim storage approach. MemPalace proved 96.6% R@5 on LongMemEval with raw verbatim storage.
- **[obsidian-mind](https://github.com/obsidian-ai/obsidian-mind)** — Vault structure (brain/work/org/perf), wikilink conventions, frontmatter validation, and session lifecycle patterns.
- **Claude Code / Anthropic** — Hook patterns and agent-tool interaction models.
- **OpenClaw** — Framework-agnostic agent runtime that inspired the "universal" design goal.

OMPA combines these into a framework-agnostic package that works with any AI agent runtime.

[![PyPI version](https://img.shields.io/pypi/v/ompa)](https://pypi.org/project/ompa/)
[![Python versions](https://img.shields.io/pypi/pyversions/ompa)](https://pypi.org/project/ompa/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](https://opensource.org/licenses/MIT)

## The Problem

Every AI agent starts empty every session. Important decisions get lost. Context grows expensive. `ANKI` prompts only get you so far.

## The Solution

OMPA gives any AI agent — **Claude Code, OpenClaw, Codex, Gemini CLI, or any custom agent** — persistent, structured memory that:

- **Never forgets a decision** (vault + knowledge graph)
- **Knows where things belong** (15 message types with routing hints)
- **Survives context compaction** (verbatim storage, no summarization loss)
- **Works offline** (local sentence-transformers, zero API cost)

## Quick Start

```bash
pip install ompa

# Initialize a vault
ao init ./workspace

# Run your agent session, then:
ao session-start     # ~2K token context injection
ao classify "We decided to go with Postgres"   # Route to right folder
ao search "authentication decisions"           # Semantic search
ao kg-query Kai       # Query the knowledge graph
ao wrap-up            # Session summary + save to vault
```

## How It Works

### Three-Layer Memory Architecture

```
┌─────────────────────────────────────────────────────────┐
│  Layer 1: Vault (human-navigable markdown)              │
│  brain/  work/  org/  perf/  ← obsidian-mind structure  │
├─────────────────────────────────────────────────────────┤
│  Layer 2: Palace (agent-accessible metadata)            │
│  wings → rooms → drawers (vault file references)       │
│  halls: facts, events, discoveries, preferences         │
│  tunnels: cross-wing connections                       │
├─────────────────────────────────────────────────────────┤
│  Layer 3: Knowledge Graph (temporal triples)             │
│  SQLite: subject → predicate → object + validity window │
│  Query entity history at any point in time             │
└─────────────────────────────────────────────────────────┘
```

### 5 Lifecycle Hooks

| Hook | Tokens | When |
|------|--------|------|
| `session_start` | ~2K | Session begins |
| `user_message` | ~100 | Each user message |
| `post_tool` | ~200 | After each tool call |
| `pre_compact` | ~100 | Before context compaction |
| `stop` | ~500 | Session ends |

### 15 Message Types

DECISION, INCIDENT, WIN, LOSS, BLOCKER, QUESTION, SUGGESTION, REVIEW, BUG, FEATURE, LEARN, RETROSPECTIVE, ALERT, STATUS, CHORE — each with routing hints that automatically file things in the right place.

### Semantic Search (Zero API Cost)

Uses `sentence-transformers` (all-MiniLM-L6-v2) locally. No OpenAI/Anthropic API calls for search.

## Architecture

```
agnostic_obsidian/
├── core.py              # OMPA main class
├── vault.py             # Vault management (brain/work/org/perf)
├── palace.py            # Palace metadata (wings/rooms/drawers)
├── knowledge_graph.py   # Temporal KG (SQLite triples)
├── hooks.py             # 5 lifecycle hooks
├── classifier.py       # 15 message types
├── semantic.py          # Local semantic search
├── mcp_server.py        # MCP protocol server (15 tools)
└── cli.py              # 14 CLI commands
```

## MCP Server (15 Tools)

Works with **Claude Desktop, Cursor, Windsurf** natively:

```bash
# Claude Desktop
claude mcp add ompa -- python -m agnostic_obsidian.mcp_server
```

Tools: `ao_session_start`, `ao_classify`, `ao_search`, `ao_kg_query`, `ao_kg_add`, `ao_palace_wings`, `ao_palace_rooms`, `ao_palace_tunnel`, `ao_validate`, `ao_wrap_up`, `ao_status`, `ao_orphans`, `ao_init`, `ao_search`, `ao_stop`

## Python API

```python
from agnostic_obsidian import OMPA

ao = OMPA(vault_path="./workspace")

# Lifecycle
result = ao.session_start()       # Returns ~2K token context injection
hint = ao.handle_message("We won the enterprise deal!")
ao.post_tool("write", {"file_path": "work/active/auth.md"})
ao.stop()

# Search
results = ao.search("authentication decisions", wing="Orion")

# Knowledge Graph
ao.kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
triples = ao.kg.query_entity("Kai")
timeline = ao.kg.timeline("Orion")

# Palace
ao.palace.create_wing("Orion", type="project")
ao.palace.create_tunnel("Kai", "Orion", "auth-migration")
traversal = ao.palace.traverse("Orion", "auth-migration")
```

## CLI Commands

```
ao init          ao status      ao session-start  ao classify
ao search        ao orphans     ao wrap-up        ao wings
ao rooms         ao tunnel      ao kg-query       ao kg-timeline
ao kg-stats      ao validate    ao rebuild-index
```

## Framework Agnostic

Unlike MemPalace (Claude Code + MCP only) or obsidian-mind (Claude Code hooks only), OMPA works with **any AI agent**:

| Agent | Integration |
|-------|-------------|
| OpenClaw | Python API or MCP server |
| Claude Code | Python API or MCP server |
| Codex | Python API or MCP server |
| Gemini CLI | Python API or MCP server |
| Custom agent | Python API |

## Installation

```bash
pip install ompa        # Core only
pip install ompa[all]   # All dependencies including sentence-transformers
```

Requires Python 3.10+.

## Why "Agnostic"?

Because memory should not be coupled to your agent framework. Build once, use anywhere. The "Universal" angle is the moat — not just another Claude Code plugin.

## Comparison

| Feature | OMPA | MemPalace | obsidian-mind |
|---------|-----------------|------------|---------------|
| Framework | Any | Claude Code | Claude Code |
| Memory type | Vault + Palace + KG | Palace + KG | Vault only |
| Semantic search | Local (free) | ChromaDB API | QMD (paid) |
| Temporal KG | SQLite ✓ | SQLite ✓ | ✗ |
| MCP server | 15 tools | 15 tools | ✗ |
| CLI | 14 commands | ✗ | ✗ |
| Hooks | 5 lifecycle | 3 lifecycle | 3 lifecycle |
| Message types | 15 | 15 | 5 |
| Verbatim storage | ✓ | ✓ | ✗ |
| Multi-agent | ✓ | ✗ | ✗ |

## License

MIT — Micap AI
