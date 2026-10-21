# API Stability

OMPA follows [Semantic Versioning](https://semver.org/) starting at v1.0.0.

## What is stable

The following public API is stable. Breaking changes will only occur in a new **major version** (e.g., 1.x.x → 2.0.0), and will be documented in [CHANGELOG.md](CHANGELOG.md) with a migration guide.

### Core class — `Ompa`

```python
from ompa import Ompa

ao = Ompa(
    vault_path="./workspace",
    agent_name="agent",
    enable_semantic=True,
    embedding_backend=None,
    shared_vault_path=None,
    personal_vault_path=None,
    isolation_mode="strict",
)

# Lifecycle hooks
ao.session_start() -> HookResult
ao.handle_message(message: str) -> HookResult
ao.post_tool(tool_name: str, tool_input: dict) -> HookResult
ao.pre_compact(transcript: str) -> HookResult
ao.stop() -> HookResult
ao.wrap_up() -> HookResult   # alias for stop()
ao.standup() -> HookResult   # alias for session_start()

# Search
ao.search(query, limit, hybrid, wing, room, vaults) -> list[SearchResult]
ao.rebuild_index() -> int

# Classification
ao.classify(message: str) -> Classification
ao.get_routing_hint(message: str) -> str

# Knowledge graph
ao.kg_add(subject, predicate, object, valid_from, source) -> None
ao.kg_query(entity, as_of) -> list[Triple]
ao.kg_timeline(entity) -> list[dict]
ao.kg_populate() -> int

# Vault
ao.get_stats() -> dict
ao.find_orphans() -> list[Note]
ao.update_brain(note_name, content, append) -> None
ao.get_brain_note(name) -> Note | None

# Dual-vault
ao.write(content, file_path, tags, vault) -> dict
ao.export_to_shared(note_path, confirm, sanitize) -> dict
ao.import_to_personal(note_path, link_back) -> dict
ao.sync() -> dict
ao.migrate_to_dual_vault(shared_path, personal_path, rules) -> dict

# Properties
ao.vault -> Vault
ao.palace -> Palace
ao.kg -> KnowledgeGraph
ao.hooks -> HookManager
ao.is_dual_vault -> bool
```

### Async class — `AsyncOmpa`

```python
from ompa import AsyncOmpa
# Same interface as Ompa, all methods are async coroutines.
# async with AsyncOmpa(...) as ao: ...
```

### Data classes

```python
HookResult(hook_name, success, output, tokens_hint, error)
Classification(message_type, confidence, suggested_action, routing_hints)
SearchResult(path, content_excerpt, score, match_type)
Triple(subject, predicate, object, valid_from, valid_to, confidence, source_file)
SyncResult(success, backend, direction, files_changed, message, error, details)
MigrationReport(vault_path, detected_version, current_version, needed_migrations, warnings)
MigrationResult(success, dry_run, applied, skipped, errors)
```

### Enums

```python
MessageType      # 15 values: DECISION, INCIDENT, WIN, ...
IsolationMode    # AUTO, STRICT, MANUAL
VaultTarget      # SHARED, PERSONAL
```

### CLI commands

All `ao <command>` invocations are stable. New commands may be added; existing commands will not be removed or have incompatible flag changes within a major version.

### MCP tools

The 15 MCP tools (`ao_session_start`, `ao_classify`, etc.) are stable. New tools may be added.

### Sync backends

```python
from ompa.sync import SyncBackend, SyncResult
from ompa.sync import GitSyncBackend, S3SyncBackend, RsyncBackend
```

The `SyncBackend` ABC and `SyncResult` are stable. Backend classes are stable in their constructor signatures and the `push` / `pull` / `status` methods.

### Adapters

```python
from ompa.adapters.langchain import OmpaMemory, OmpaRetriever
from ompa.adapters.llamaindex import OmpaReader, OmpaVaultRetriever
from ompa.adapters.openai_agents import OmpaAgentHooks
from ompa.adapters.nim import NIMEmbeddingBackend
from ompa.adapters.faiss import FAISSSemanticIndex
```

These are stable at their documented public interfaces.

### Token counter

```python
from ompa import count_tokens
count_tokens(text: str, model: str) -> int
```

---

## What is NOT stable

The following are internal implementation details and may change in minor versions:

- `ompa.vault.DEFAULT_EXCLUDE_PATTERNS`
- `ompa.vault._safe_resolve`
- `ompa.knowledge_graph._row_to_triple`
- `ompa.hooks.*Hook` classes (use `HookManager.register_hook` instead)
- `ompa.semantic._cosine_similarity`
- `ompa.semantic.EmbeddingBackend` protocol (stable in interface, not in module path)
- `ompa.migration.CURRENT_SCHEMA_VERSION` (increases with migrations)
- Internal vault file layout (`.palace/` structure) — do not parse directly
  - Exception: `.palace/knowledge_graph.sqlite3` is a versioned read contract for other tools. See
    [docs/kg-schema-v1.md](docs/kg-schema-v1.md).

---

## Deprecation policy

Breaking changes at the public API level will:

1. Emit a `DeprecationWarning` for at least one minor version before removal
2. Be documented in [CHANGELOG.md](CHANGELOG.md) under `### Deprecated`
3. Include a migration note explaining what to use instead

---

## PyPI trusted publishing

Starting at v1.0.0, all releases are published via [OIDC trusted publishing](https://docs.pypi.org/trusted-publishers/) — no long-lived tokens. The GitHub Actions publish job in `.github/workflows/ci.yml` is the only authorized publisher.
