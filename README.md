# agent-memory

**Universal Agent Memory Layer** — Persistent memory for AI agents with lifecycle hooks, semantic search, and classification routing.

Built by [Micap AI](https://micap.ai) as part of our agent infrastructure. Designed to work with any AI agent framework — OpenClaw, Claude Code, Codex CLI, Gemini CLI, or custom agents.

## Why?

AI agents are powerful but they forget. Every session starts from zero. You re-explain the same things. Decisions made three conversations ago disappear. Knowledge never compounds.

**agent-memory** gives your agents a brain. Persistent memory across sessions. Structured organization. Semantic search. Token-efficient context loading.

## Features

- **Lifecycle Hooks** — `session_start`, `user_message`, `post_tool`, `pre_compact`, `stop` — exactly like obsidian-mind but framework-agnostic
- **Tiered Context Loading** — Lightweight session start (~2K tokens), targeted queries on demand
- **Message Classification** — Classifies every message and injects routing hints (~100 tokens)
- **Semantic Search** — Local embeddings (zero API cost) with hybrid keyword + semantic search
- **Structured Vault** — `brain/`, `work/`, `org/`, `perf/` organization with wikilinks
- **Python + CLI** — Use as a library or standalone

## Installation

```bash
pip install agent-memory
```

With semantic search (requires torch + sentence-transformers):
```bash
pip install agent-memory[semantic]
```

## Quick Start

### Python API

```python
from agent_memory import AgentMemory

memory = AgentMemory(vault_path="./workspace")

# At session start
result = memory.session_start()
print(result.output)  # Inject this into context

# On every user message
hint = memory.handle_message("We decided to defer the Redis migration to Q2")
print(hint.output)  # "[DECISION] Record decision and update relevant project notes"

# After writing a file
memory.post_tool("write", {"file_path": "work/active/auth-refactor.md"})

# At session end
memory.stop()
```

### CLI

```bash
# Initialize
agent-memory init --path ./workspace

# Run session start
agent-memory session-start

# Classify a message
agent-memory classify "We decided to go with Postgres"

# Search the vault
agent-memory search "what did we decide about caching"

# Check for orphans
agent-memory orphans

# Wrap up
agent-memory wrap-up
```

## Architecture

```
Session Lifecycle
─────────────────
session_start  →  Load lightweight context (~2K tokens)
                    - Vault file listing
                    - North Star goals
                    - Active work
                    - Recent git changes

user_message  →  Classify + route (~100 tokens)
                    - Decision, incident, win, question, etc.
                    - Routing hints injected into context

post_tool     →  Validate writes (~200 tokens)
                    - Check frontmatter
                    - Verify wikilinks

pre_compact   →  Archive transcript
                    - Save to thinking/session-logs/

stop          →  Wrap up checklist
                    - Verify notes have links
                    - Update indexes
                    - Spot uncaptured wins
```

## Vault Structure

```
workspace/
├── brain/              # Agent's operational memory
│   ├── North Star.md   # Goals, focus areas
│   ├── Key Decisions.md
│   ├── Patterns.md
│   ├── Gotchas.md
│   └── Memories.md
├── work/               # Work notes
│   ├── active/         # Current projects
│   ├── archive/        # Completed
│   ├── incidents/      # Incident docs
│   └── 1-1/            # Meeting notes
├── org/                # People, teams
│   ├── people/
│   └── teams/
├── perf/               # Performance, reviews
│   ├── competencies/
│   └── brag/
├── thinking/           # Scratchpads
│   └── session-logs/  # Archived transcripts
└── templates/          # Note templates
```

## Message Classification

Every message is classified into one of:

| Type | Routing |
|------|---------|
| `DECISION` | Record in brain/Key Decisions.md |
| `INCIDENT` | Create in work/incidents/ |
| `WIN` | Add to perf/Brag Doc.md |
| `ONE_ON_ONE` | Update work/1-1/ |
| `MEETING` | Create in work/meetings/ |
| `PROJECT_UPDATE` | Update work/active/ |
| `PERSON_INFO` | Update org/people/ |
| `QUESTION` | Search vault first |
| `TASK` | Add to task list |
| `ARCHITECTURE` | Create ADR in work/active/ |
| `BRAIN_DUMP` | Route to appropriate notes |
| `WRAP_UP` | Run /om-wrap-up |
| `STANDUP` | Run /om-standup |

## Semantic Search

Uses local `sentence-transformers` for zero API cost:

```python
results = memory.search("what did we decide about caching")
# Returns: [(path, excerpt, score, match_type), ...]

# Or use qsearch (QMD-style)
results = memory.qsearch("architecture decisions for auth")
```

## Hook System

Customize behavior by registering your own hooks:

```python
from agent_memory.hooks import Hook, HookContext, HookResult

class MyHook(Hook):
    def execute(self, context: HookContext) -> HookResult:
        return HookResult(
            hook_name="my_hook",
            success=True,
            output="Custom output"
        )

memory = AgentMemory("./workspace")
memory.hooks.register_hook("my_hook", MyHook())
```

## OpenClaw Integration

```python
# In your OpenClaw agent, call hooks at appropriate points:

from agent_memory import AgentMemory

memory = AgentMemory(vault_path="/path/to/workspace")

# On session start
result = memory.session_start()
# Inject result.output into system prompt

# On user message
hint = memory.handle_message(user_message)
# Include hint.output in context

# On file write
memory.post_tool("write", {"file_path": file_path})

# On session end
memory.stop()
```

## For Developer Portfolio

This tool is designed to be:
- **Framework-agnostic** — Works with any AI agent (OpenClaw, Claude Code, Codex, Gemini CLI)
- **Self-hosted** — No external API dependencies for core functionality
- **Token-efficient** — Designed for cost-conscious deployments
- **Extensible** — Easy to add custom hooks and classifiers

Perfect for demonstrating skills in AI agent systems, memory architecture, and production-grade Python.

## License

MIT

---

Built with ❤️ by [Micap AI](https://micap.ai)
