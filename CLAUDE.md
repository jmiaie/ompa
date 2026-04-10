# CLAUDE.md — OMPA

You are building **OMPA** (Obsidian-MemPalace-Agnostic) — a universal AI agent memory layer.

## What It Does

Gives any AI agent persistent memory with:
- **Vault** (obsidian-mind conventions): brain/work/org/perf folders, wikilinks, frontmatter
- **Palace** (MemPalace): wings/rooms/closets/drawers metadata layer
- **Knowledge Graph**: temporal SQLite triples with validity windows
- **5 Lifecycle hooks**: session_start, user_message, post_tool, pre_compact, stop
- **15 Message types**: with routing hints (DECISION, INCIDENT, WIN, etc.)
- **Semantic search**: local sentence-transformers (zero API cost)
- **MCP server**: 14 tools via Model Context Protocol

## Package Structure

```
ompa/
├── __init__.py          # Public API exports
├── __main__.py          # python -m ompa support
├── core.py              # Ompa main class
├── vault.py             # Vault management
├── palace.py            # Palace metadata (wings/rooms/closets/drawers)
├── knowledge_graph.py   # Temporal KG (SQLite triples)
├── hooks.py             # 5 lifecycle hooks + HookManager
├── classifier.py        # 15 message types + multilingual
├── semantic.py          # Local semantic search
├── mcp_server.py        # MCP protocol server (14 tools)
└── cli.py               # typer CLI
```

## Key APIs

```python
from ompa import Ompa

ao = Ompa(vault_path="./workspace")

# Lifecycle
result = ao.session_start()    # ~2K tokens
result = ao.handle_message(msg) # ~100 tokens
ao.post_tool("write", {"file_path": "work/active/auth.md"})
ao.stop()

# Palace
ao.palace.list_wings()
ao.palace.create_tunnel("Kai", "Orion", "auth-migration")

# KG
ao.kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
ao.kg.query_entity("Kai")

# Search
ao.search("authentication", wing="Orion", room="auth-migration")
```

## MCP Server

```bash
# Claude Desktop
claude mcp add ompa -- python -m ompa.mcp_server

# Tools available:
# ao_session_start, ao_classify, ao_search, ao_kg_query,
# ao_kg_add, ao_kg_stats, ao_palace_wings, ao_palace_rooms,
# ao_palace_tunnel, ao_validate, ao_wrap_up, ao_status, ao_orphans, ao_init
```

## Architecture Decisions

1. **Lazy semantic loading**: `SemanticIndex._model` loaded on first access
2. **Framework-agnostic**: Pure Python, no agent SDK deps
3. **Vault + Palace dual-layer**: Vault=source of truth, Palace=retrieval acceleration
4. **Verbatim storage**: No summarization (proven 96.6% R@5 by MemPalace)
5. **Temporal KG**: SQLite triples with validity windows
6. **Path traversal guards**: All vault file ops resolve + boundary-check paths
7. **Context managers**: All SQLite connections use `with` for leak-free operation

## Development

```bash
pip install -e ".[all]"   # Install with all deps
pip install -e ".[dev]"   # Dev deps

# Run tests
pytest tests/ -v

# CLI
ao init
ao status
ao classify "message"
ao wings
ao kg-query "Entity"
```

## Adding Message Types

1. Add enum to `MessageType` in `classifier.py`
2. Add regex patterns to `PATTERNS[MessageType]`
3. Add routing hints to `ROUTING_HINTS[MessageType]`
4. Add folder to `FOLDER_MAP[MessageType]`

## Adding Custom Hooks

```python
from ompa.hooks import Hook, HookContext, HookResult

class MyHook(Hook):
    def __init__(self):
        super().__init__("my_hook", token_budget=50)
    def execute(self, context: HookContext, **kwargs) -> HookResult:
        return HookResult(hook_name=self.name, success=True, output="...", tokens_hint=50)

ao = Ompa("./workspace")
ao.hooks.register_hook("my_hook", MyHook())
```
