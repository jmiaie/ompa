# CLAUDE.md — agent-memory

You are building a Python package called **agent-memory** — a universal agent memory layer.

## What It Does

Gives any AI agent persistent memory across sessions with:
- **Lifecycle hooks**: session_start, user_message, post_tool, pre_compact, stop
- **Message classification**: 15 types (DECISION, INCIDENT, WIN, QUESTION, etc.) with routing hints
- **Semantic search**: local embeddings via sentence-transformers (zero API cost)
- **Structured vault**: brain/, work/, org/, perf/ organization

## Package Structure

```
agent_memory/
├── __init__.py        # Public API exports
├── core.py            # AgentMemory main class
├── vault.py           # Vault management, Note class
├── hooks.py           # 5 lifecycle hooks + HookManager
├── classifier.py      # Message classification (15 types)
├── semantic.py        # Semantic search index
└── cli.py             # typer CLI (init, session-start, classify, search, etc.)
```

## Key APIs

```python
from agent_memory import AgentMemory

memory = AgentMemory(vault_path="./workspace")

# Lifecycle hooks
result = memory.session_start()  # ~2K tokens context
result = memory.handle_message("We decided to defer Redis to Q2")  # ~100 tokens
result = memory.post_tool("write", {"file_path": "work/active/auth.md"})
result = memory.pre_compact(transcript)
memory.stop()  # wrap-up

# Classification
c = memory.classify("We won the client deal!")
# c.message_type → WIN, c.routing_hints → ["Add to perf/Brag Doc.md", ...]

# Search
results = memory.search("what did we decide about caching")
```

## Architecture Decisions

- **Lazy model loading**: SemanticIndex._model is loaded on first access, not at __init__
- **Graceful fallbacks**: If frontmatter parsing fails, read raw content
- **Token budgets per hook**: session_start=2000, user_message=100, post_tool=200, stop=500
- **Framework-agnostic**: No OpenClaw, Claude Code, or any agent framework deps — works with anything

## Development

```bash
# Install
pip install -e ".[dev]"

# Run tests
pytest

# Build
python -m build

# CLI
agent-memory --help
agent-memory classify "your message here"
```

## Adding New Message Types

1. Add to `MessageType` enum in `classifier.py`
2. Add patterns to `PATTERNS` dict (regex list)
3. Add routing hints to `ROUTING_HINTS` dict
4. Add folder to `FOLDER_MAP`
5. Add action to `_get_action()` method

## Adding Custom Hooks

```python
from agent_memory.hooks import Hook, HookContext, HookResult

class MyHook(Hook):
    def execute(self, context: HookContext) -> HookResult:
        return HookResult(
            hook_name="my_hook",
            success=True,
            output="custom output",
            tokens_hint=50
        )

memory = AgentMemory("./workspace")
memory.hooks.register_hook("my_hook", MyHook())
```
