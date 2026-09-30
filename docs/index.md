# OMPA

**Universal AI Agent Memory Layer** — Vault · Palace · Temporal Knowledge Graph

[![CI](https://github.com/jmiaie/ompa/actions/workflows/ci.yml/badge.svg)](https://github.com/jmiaie/ompa/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/ompa?color=blue)](https://pypi.org/project/ompa/)
[![Python](https://img.shields.io/pypi/pyversions/ompa)](https://pypi.org/project/ompa/)
[![License](https://img.shields.io/pypi/l/ompa)](https://github.com/jmiaie/ompa/blob/main/LICENSE)

---

OMPA gives any AI agent persistent memory in one `pip install`. It combines three layers:

```
┌──────────────────────────────────────────────────────┐
│  Layer 1: Vault  — human-navigable markdown          │
├──────────────────────────────────────────────────────┤
│  Layer 2: Palace — agent-accessible metadata         │
├──────────────────────────────────────────────────────┤
│  Layer 3: Knowledge Graph — temporal SQLite triples  │
└──────────────────────────────────────────────────────┘
```

**96.6% R@5 on LongMemEval** using verbatim storage — no summarization loss, no API cost for search.

## Install

```bash
pip install ompa          # Core
pip install ompa[all]     # Core + local semantic search
```

## Quick start

```bash
ao init
ao session-start
ao classify "We decided to use Postgres"
ao search "database decisions"
ao wrap-up
```


## Why OMPA (vs raw Obsidian)?

For consulting leads: **[Why OMPA vs raw Obsidian?](why-ompa.md)** — one page on the agent-memory wedge, dual-vault hygiene, and the Locus / CognitionOS companion stack.

## Companion stack

| Layer | Project | Role |
|---|---|---|
| Memory | **OMPA** | Vault + palace + temporal KG |
| Retrieval | **[Locus](https://github.com/jmiaie/locus)** | Vectorless, explainable RAG |
| Compliance | **[CognitionOS](https://github.com/jmiaie/cognition-os)** | Audit / RBAC / retention productization |

## What's next

- [Why OMPA vs raw Obsidian?](why-ompa.md) — consulting one-pager
- [Quickstart guide](quickstart.md) — detailed walkthrough
- [Lifecycle Hooks](guides/hooks.md) — wire OMPA into any agent
- [MCP Server](guides/mcp.md) — Claude Desktop / Cursor setup
- [API Reference](api/ompa.md) — full Python API docs
