# Prompt for Claude Code — Build agent-memory

Copy and paste this into Claude Code to reproduce the entire project:

---

## Task

Build a Python package called **agent-memory** — a universal agent memory layer for AI agents. It provides persistent memory across sessions with lifecycle hooks, message classification, and semantic search. The package should work with any AI agent framework (OpenClaw, Claude Code, Codex, Gemini CLI, etc.).

## Requirements

### Core Functionality

1. **Vault Management** (`vault.py`)
   - Create folder structure: `brain/`, `work/active/`, `work/archive/`, `work/incidents/`, `work/1-1/`, `org/people/`, `org/teams/`, `perf/competencies/`, `perf/brag/`, `thinking/`, `templates/`
   - `Note` class: path, frontmatter (via python-frontmatter), content, wikilinks (regex `\[\[([^\]]+)\]\]`)
   - `Vault` class: list_notes(), find_orphans(), search_by_name(), update_brain_note(), create_from_template(), get_stats()
   - Graceful fallback: if frontmatter parsing fails, read raw content

2. **Message Classification** (`classifier.py`)
   - 15 message types: DECISION, INCIDENT, WIN, ONE_ON_ONE, MEETING, PROJECT_UPDATE, PERSON_INFO, QUESTION, TASK, ARCHITECTURE, CODE, BRAIN_DUMP, WRAP_UP, STANDUP, UNKNOWN
   - Each type has regex patterns, routing hints, suggested folder, suggested action
   - `MessageClassifier.classify(message)` → `Classification(message_type, confidence, routing_hints, suggested_folder, suggested_action)`
   - Confidence normalized 0-1, reduced for short messages (<5 words)

3. **Lifecycle Hooks** (`hooks.py`)
   - 5 hooks: `session_start`, `user_message`, `post_tool`, `pre_compact`, `stop`
   - `Hook` base class with `execute(context) → HookResult`
   - `HookResult`: hook_name, success, output, tokens_hint, error
   - `HookContext`: vault_path, session_id, timestamp, agent_name, memory
   - `HookManager`: runs all hooks, manages session_id/timestamp

   Hook behaviors:
   - `session_start`: Load vault listing, North Star (extract "Current Focus"), recent git log (48h), active work notes, vault stats. Budget: ~2000 tokens.
   - `user_message`: Classify message, inject routing hint. Budget: ~100 tokens.
   - `post_tool`: Validate .md writes — check frontmatter (date, description fields), wikilinks. Budget: ~200 tokens.
   - `pre_compact`: Archive session transcript to `thinking/session-logs/{timestamp}_{session_id}.json`. Budget: ~100 tokens.
   - `stop`: Find orphans, update brain index, check North Star exists. Budget: ~500 tokens.

4. **Semantic Search** (`semantic.py`)
   - Use `sentence-transformers` (all-MiniLM-L6-v2) for local embeddings — zero API cost
   - **Lazy loading**: model loaded on first access via `@property model`, not in `__init__`
   - Index vault into chunks (512 words each), store as JSON
   - `search(query, limit=5, hybrid=True)`: semantic similarity + keyword boost
   - Fallback to keyword grep if model unavailable
   - `SearchResult`: path, content_excerpt, score, match_type

5. **Main Class** (`core.py`)
   - `AgentMemory(vault_path, agent_name="agent", enable_semantic=True)`
   - Methods: `session_start()`, `handle_message(message)`, `post_tool(tool_name, tool_input)`, `pre_compact(transcript)`, `stop()`, `wrap_up()`, `standup()`
   - `classify(message)`, `get_routing_hint(message)`, `last_classification`
   - `search(query, limit=5, hybrid=True)`, `qsearch(query, limit=5)`
   - `rebuild_index()`, `get_stats()`, `find_orphans()`, `update_brain()`, `get_brain_note()`, `create_note()`

6. **CLI** (`cli.py`)
   - Use `typer` framework
   - 7 commands: `init`, `session-start`, `classify`, `search`, `stats`, `orphans`, `wrap-up`, `rebuild-index`
   - Use `rich` for formatted table output

7. **Package** (`__init__.py`)
   - Export: `AgentMemory`, `Vault`, `Note`, `VaultConfig`, `MessageClassifier`, `Classification`, `MessageType`, `HookManager`, `HookContext`, `HookResult`, `Hook`, `SemanticIndex`, `SearchResult`
   - Version: `__version__ = "0.1.0"`

### Project Files

- `pyproject.toml`: name="agent-memory", requires-python >=3.10, dependencies: numpy, sentence-transformers, torch, typer, rich, python-frontmatter, watchdog. Optional dev: pytest, black, ruff
- `README.md`: Full documentation with architecture diagram, usage examples, CLI reference
- `CLAUDE.md`: Context for Claude Code working on this repo (architectural decisions, APIs, how to add types/hooks)
- `LICENSE`: MIT
- `.gitignore`: Standard Python ignores + `.agent-memory/` index dir

### Vault Structure for Examples

```
examples/simple_vault/
├── brain/
│   └── (empty initially)
└── demo.py  # Run this to see lifecycle in action
```

The demo.py should:
1. Initialize AgentMemory
2. Run session_start and print output
3. Classify 6 messages showing all types
4. Run stop and print output

## Architecture Constraints

- **Framework-agnostic**: No dependencies on OpenClaw, Claude Code, or any specific agent framework
- **Token-efficient**: Each hook has a token budget; session_start is ~2K, user_message ~100
- **Zero API cost for core**: Semantic search uses local model, not cloud API
- **Lazy loading**: SemanticIndex model loaded on first access, not at import
- **Graceful degradation**: If frontmatter fails, read raw. If semantic model fails, keyword search.

## What This Demonstrates

- Python package architecture and API design
- CLI development with typer + rich
- Semantic search integration (sentence-transformers)
- Text classification (regex + scoring)
- Lifecycle hook patterns
- Memory system design
- Error handling and fallbacks

## After Building

Test with:
```bash
pip install -e ".[dev]"
python examples/simple_vault/demo.py
agent-memory classify "We decided to go with Postgres"
agent-memory stats
```
