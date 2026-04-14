# AgnosticObsidian — Claude Code Project Prompt

Build **AgnosticObsidian** — a universal AI agent memory layer that hybridizes two proven systems:

1. **obsidian-mind** (Claude Code vault system): lifecycle hooks, session workflows, message classification, wikilink conventions, structured vault folders
2. **MemPalace** (96.6% LongMemEval benchmark): Palace structure (wings/rooms/closets/drawers), temporal knowledge graph, verbatim storage, halls + tunnels navigation

Rebuild from scratch as a **framework-agnostic Python package** — works with OpenClaw, Claude Code, Codex, Gemini CLI, or any custom AI agent. No vendor lock-in.

## Name: AgnosticObsidian

**Agnostic** = framework-agnostic (Node.js, Python, any agent)
**Obsidian** = vault + wikilinks + brain folder conventions

---

## What Makes It Different

| obsidian-mind | MemPalace | AgnosticObsidian |
|---------------|-----------|------------------|
| Claude Code only | Claude Code + MCP | Any AI agent |
| Node.js hooks | Bash hooks | Python hooks + CLI |
| Summarization | Raw verbatim storage | Raw verbatim + structured KG |
| Folder hierarchy | Palace (wings/rooms) | Hybrid: vault folders + Palace metadata |
| Single agent | Multi-agent diaries | Universal hooks + per-agent wings |
| No KG | Temporal KG (SQLite) | Temporal KG + wikilinks |
| QMD search | ChromaDB semantic | Local embeddings (zero API cost) |

**Key insight from MemPalace**: Store everything verbatim, let structure make it findable. Do not summarize — you lose context.

**Key insight from obsidian-mind**: Hooks + routing + conventions = agent discipline. The agent does not decide what is important — the system routes everything to the right place.

---

## Package Structure

```
agnostic_obsidian/
├── __init__.py              # Public API exports, version
├── core.py                   # AgnosticObsidian main class
├── vault.py                  # Vault management (obsidian-mind conventions)
├── palace.py                 # Palace structure (MemPalace wings/rooms/closets/drawers)
├── hooks.py                  # 5 lifecycle hooks + HookManager
├── classifier.py             # Message classification (15 types + multilingual)
├── knowledge_graph.py        # Temporal KG (MemPalace SQLite triples)
├── semantic.py               # Semantic search (local sentence-transformers)
├── palace_graph.py           # Hall + tunnel navigation graph
├── dialect.py                # AAAK compression (experimental, optional)
├── cli.py                    # typer CLI (11 commands)
└── examples/
    └── simple_vault/
        └── demo.py           # Full lifecycle demo
```

---

## Core Concepts

### The Vault (from obsidian-mind)

Standardized folder hierarchy:

```
workspace/
├── brain/                    # Agent operational memory
│   ├── North Star.md         # Goals, current focus
│   ├── Key Decisions.md      # Architectural + project decisions
│   ├── Patterns.md           # Reusable workflows
│   ├── Gotchas.md            # Lessons learned
│   ├── Memories.md           # Topic index
│   └── Skills.md            # Vault-specific commands
├── work/
│   ├── active/              # Current projects (1-5 files max)
│   ├── archive/YYYY/        # Completed by year
│   ├── incidents/           # Incident docs
│   ├── 1-1/                 # Meeting notes
│   └── meetings/            # Raw meeting exports inbox
├── org/
│   ├── people/              # One note per person
│   └── teams/               # One note per team
├── perf/
│   ├── competencies/        # Atomic competency definitions
│   ├── brag/                # Quarterly wins
│   └── evidence/            # PR scans, data extracts
├── thinking/
│   └── session-logs/        # Archived transcripts
└── templates/               # Note templates
```

**Wikilink convention**: Every note must link to at least one other note. No orphans.

### The Palace (from MemPalace)

Metadata layer on top of the vault:

```
WING = person OR project
  └── ROOM = specific topic (auth-migration, sprint-planning)
       └── CLOSET = summary pointing to verbatim drawer
            └── DRAWER = original verbatim file

HALLS = memory types within every wing:
  - hall_facts       (decisions made, choices locked)
  - hall_events      (sessions, milestones, debugging)
  - hall_discoveries (breakthroughs, insights)
  - hall_preferences (habits, likes, opinions)
  - hall_advice      (recommendations)

TUNNELS = cross-wing connections:
  wing_kai/hall_events/auth-migration <-> wing_orion/hall_facts/auth-migration
```

The palace adds +34% retrieval improvement on top of raw search.

### Temporal Knowledge Graph (from MemPalace)

SQLite-based entity-relationship triples with validity windows:

```python
kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
kg.invalidate("Kai", "works_on", "Orion", ended="2026-03-01")

kg.query_entity("Kai")                     # Current facts only
kg.query_entity("Kai", as_of="2026-01-15")  # Historical query
kg.timeline("Orion")                      # Chronological project story
```

---

## Core APIs

### AgnosticObsidian (core.py)

```python
from agnostic_obsidian import AgnosticObsidian

ao = AgnosticObsidian(vault_path="./workspace")

# Lifecycle hooks (any agent framework)
result = ao.session_start()      # ~2K tokens: vault + North Star + active work
result = ao.handle_message("We decided to go with Postgres")  # ~100 tokens
result = ao.post_tool("write", {"file_path": "work/active/auth.md"})
result = ao.pre_compact(transcript)
ao.stop()                        # wrap-up checklist

# Palace navigation
ao.palace.list_wings()
ao.palace.find_tunnel("Kai", "Orion")

# Knowledge graph
ao.kg.add_triple("Entity", "relation", "Target", valid_from="2026-04-10")
ao.kg.query_entity("Entity")

# Classification (15 types, multilingual)
c = ao.classify("We won the enterprise deal!")
# c.message_type -> WIN
# c.routing_hints -> ["Add to perf/brag/Q1-2026.md", ...]

# Search (local embeddings, zero API cost)
results = ao.search("auth decision", wing="Orion", room="auth-migration")
```

### Vault (vault.py)

```python
from agnostic_obsidian import Vault

vault = Vault("./workspace")
vault.list_notes(exclude=[".git", ".claude", "thinking"])
vault.find_orphans()                           # notes with no wikilinks
vault.validate_write("work/active/auth.md")   # frontmatter + wikilink check
vault.update_brain_note("Key Decisions", content)
vault.get_brain_note("North Star")
vault.create_from_template("Decision Record", "work/active/ADR-001.md")
vault.get_stats()                              # {total_notes, orphans, brain_notes}
```

### Palace (palace.py)

```python
from agnostic_obsidian import Palace

palace = Palace("./workspace/.palace")

palace.create_wing("Orion", type="project", keywords=["orion", "analytics"])
palace.create_wing("Kai", type="person", keywords=["kai"])
palace.list_wings()

palace.create_room("Orion", "auth-migration")
palace.link_drawer("Orion", "auth-migration", "work/active/auth-migration.md")

palace.add_hall("Orion", "auth-migration", "hall_facts",
                content="Team chose Clerk over Auth0...")
palace.get_hall("Orion", "auth-migration", "hall_facts")

palace.create_tunnel("Kai", "Orion", "auth-migration")
palace.find_tunnels("Kai", "Orion")
palace.traverse("Orion", "auth-migration")
```

### Knowledge Graph (knowledge_graph.py)

```python
from agnostic_obsidian import KnowledgeGraph

kg = KnowledgeGraph(db_path="./workspace/.palace/knowledge_graph.sqlite3")

kg.add_triple("Kai", "recommended", "Clerk", valid_from="2026-01-15",
              source="work/incidents/auth-incident-001.md")
kg.add_triple("Clerk", "chosen_over", "Auth0", valid_from="2026-01-15",
              source="work/active/auth-migration.md")

kg.query_entity("Kai")
kg.query_entity("Kai", as_of="2026-01-20")
kg.timeline("Orion")
kg.invalidate("Kai", "works_on", "Orion", ended="2026-03-01")
kg.stats()
```

### Semantic Search (semantic.py)

```python
# Local sentence-transformers (all-MiniLM-L6-v2) — zero API cost
# Lazy loading: model loaded on first access, not at __init__

index = SemanticIndex(Path("./workspace/.palace/semantic_index"))
index.index_vault(Path("./workspace"), exclude=[".git", ".claude", "thinking"])
index.save_index()

results = index.search("authentication", wing="Orion", room="auth-migration", limit=10)
results = index.search("auth decision", hybrid=True, limit=5)
```

### Classifier (classifier.py)

15 message types with multilingual patterns (English, Japanese, Korean, Chinese):

| Type | Routing |
|------|---------|
| DECISION | Decision Record in work/active/ + brain/Key Decisions.md |
| INCIDENT | work/incidents/ + brain/Gotchas.md |
| WIN | perf/brag/ current quarter + competency evidence |
| ONE_ON_ONE | work/1-1/ + org/people/ person note |
| MEETING | work/meetings/ inbox |
| PROJECT_UPDATE | work/active/ project note |
| PERSON_CONTEXT | org/people/ person note |
| QUESTION | Search vault first |
| TASK | Add to task list |
| ARCHITECTURE | reference/ or Decision Record |
| CODE | Update code docs |
| BRAIN_DUMP | Route to appropriate notes |
| WRAP_UP | wrap-up checklist |
| STANDUP | morning context |
| UNKNOWN | Continue conversation |

```python
c = ao.classify("Sarah mentioned Kai approved the Clerk migration")
# c.message_type -> PERSON_CONTEXT
```

### Hooks (hooks.py)

5 lifecycle hooks, framework-agnostic (pure Python, no agent SDK):

```python
# Each hook: execute(context) -> HookResult(hook_name, success, output, tokens_hint, error)

# session_start (~2000 tokens):
# - Vault file listing (first 30 files)
# - North Star "Current Focus" section
# - Recent git log (48h)
# - Active work notes
# - Palace wing overview
# - KG stats (entity count, recent facts)

# user_message (~100 tokens):
# - Classify message type
# - Inject routing hint

# post_tool (~200 tokens):
# - Validate .md writes (frontmatter + wikilinks)
# - Flag orphans
# - Auto-add to palace (wing/room/drawer)

# pre_compact (~100 tokens):
# - Archive session transcript
# - Add session summary to KG

# stop (~500 tokens):
# - Orphan check
# - North Star existence check
# - KG update with session facts
# - Palace stats
```

**Framework integration:**

```python
# OpenClaw, Claude Code, Codex, Gemini CLI, any agent
from agnostic_obsidian import AgnosticObsidian
ao = AgnosticObsidian(vault_path="/path/to/workspace")

result = ao.session_start()    # inject result.output into system prompt
hint = ao.handle_message(msg)  # include hint.output in context
ao.post_tool(tool, input)      # after file writes
ao.stop()                      # at session end
```

---

## CLI Commands (cli.py)

```bash
ao init                       # Initialize vault + palace structure
ao status                     # Vault + palace + KG overview
ao session-start              # Run session_start hook
ao classify "message"          # Classify a message
ao wrap-up                    # Run stop hook
ao wings                      # List all wings
ao rooms <wing>               # List rooms in a wing
ao tunnel <wing_a> <wing_b>  # Find tunnels between wings
ao traverse <wing> <room>    # Walk palace from a room
ao kg query "Entity"           # Query entity facts
ao kg timeline "Entity"       # Entity timeline
ao kg stats                   # KG overview
ao search "query"             # Semantic search
ao orphans                    # Find orphan notes
ao validate                   # Validate all notes
ao rebuild-index              # Rebuild semantic index
```

---

## pyproject.toml

```toml
[project]
name = "agnostic-obsidian"
version = "0.1.0"
description = "Universal AI agent memory layer — vault + palace + temporal KG"
readme = "README.md"
license = {text = "MIT"}
requires-python = ">=3.10"

dependencies = [
    "numpy>=1.24.0",
    "sentence-transformers>=2.2.0",
    "chromadb>=0.4.0",
    "typer>=0.9.0",
    "rich>=13.0.0",
    "python-frontmatter>=1.1.0",
    "watchdog>=3.0.0",
]

[project.optional-dependencies]
dev = ["pytest>=7.0.0", "pytest-asyncio>=0.21.0", "black>=23.0.0", "ruff>=0.1.0"]

[project.scripts]
ao = "agnostic_obsidian.cli:main"
```

---

## Key Architecture Decisions

### 1. Lazy model loading
SemanticIndex._model loaded via @property on first access. Import-time cost: ~0ms if enable_semantic=False.

### 2. Framework-agnostic hooks
Hooks are pure Python callables. No hook_event_name, no tool_input JSON from stdin. Any agent calls ao.session_start() and gets structured output.

### 3. Vault + Palace dual-layer
- **Vault**: human-navigable markdown files with wikilinks
- **Palace**: structured metadata (wings/rooms/halls/tunnels) for programmatic retrieval
- Vault is the source of truth. Palace is the retrieval acceleration layer.

### 4. Verbatim storage > summarization
MemPalace proved 96.6% R@5 with raw verbatim storage. Store the actual conversation, not an LLM summary. Palace metadata makes it findable.

### 5. Temporal KG as first-class citizen
Entities and triples are as important as files and wikilinks.

---

## Testing Criteria

```bash
pip install -e ".[dev]"

# CLI smoke test
ao --help
ao init
ao status
ao classify "We decided to go with Postgres"
ao wings

# Python smoke test
python -c "
from agnostic_obsidian import AgnosticObsidian

ao = AgnosticObsidian('./workspace', enable_semantic=False)

r = ao.session_start()
assert r.success

c = ao.classify('We won the enterprise deal!')
assert c.message_type.value == 'win'

r = ao.stop()
assert r.success

print('ALL TESTS PASSED')
"
```

---

## What This Demonstrates

| Skill | Evidence |
|-------|----------|
| Python package architecture | pyproject.toml, module structure, __init__.py exports |
| API design | Clean public API, dataclasses, type hints |
| CLI development | typer + rich, 11 commands |
| Semantic search | sentence-transformers, local embeddings |
| Knowledge graph | Temporal SQLite KG with validity windows |
| Palace structure | Wings/rooms/closets/drawers/halls/tunnels |
| Text classification | 15 types, regex + multilingual patterns |
| Lifecycle hook patterns | 5 hooks with budgets and context |
| Memory system design | Tiered context, verbatim storage, KG integration |
| Error handling | Graceful fallbacks throughout |
| Documentation | README, CLAUDE.md, inline docs |
| Hybrid architecture | Best of obsidian-mind + MemPalace |

**Good for**: AI engineering roles, agent frameworks, developer tooling, quant trading infrastructure.
