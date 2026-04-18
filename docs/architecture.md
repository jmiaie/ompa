# Architecture

OMPA is a local-first memory layer built from five composable subsystems.
Nothing leaves the host unless the user explicitly exports it.

```
┌────────────────────────────────────────────────────────────────┐
│                       Ompa (core.py)                           │
│   high-level facade: session_start / handle_message / write    │
└──────────────────────┬─────────────────────────────────────────┘
                       │
        ┌──────────────┼───────────────┬───────────────┬─────────┐
        ▼              ▼               ▼               ▼         ▼
   ┌─────────┐    ┌─────────┐    ┌───────────┐   ┌──────────┐ ┌──────┐
   │  Vault  │    │ Palace  │    │    KG     │   │ Semantic │ │Class-│
   │ (notes) │    │ (wings/ │    │ (triples, │   │  (vector │ │ ifier│
   │         │    │  rooms) │    │  temporal)│   │   index) │ │      │
   └────┬────┘    └────┬────┘    └─────┬─────┘   └────┬─────┘ └──────┘
        │              │               │              │
        ▼              ▼               ▼              ▼
    `.md` files    palace.json    knowledge_       embeddings.npy
    (Obsidian      (single         graph.sqlite3   + meta.json
     vault)         JSON blob)     (WAL mode)      (v2 binary)
```

## Subsystems

### Vault — `ompa/vault.py`
Obsidian-compatible `.md` files with YAML frontmatter. Responsible for:

- Note parsing (`_fast_parse_frontmatter` + CSafeLoader fast-path,
  `python-frontmatter` fallback).
- Wikilink extraction with alias/`.md`-suffix normalization.
- Orphan detection + stats.
- mtime-keyed note cache — `list_notes`, `find_orphans`, `get_stats` share
  a single parsed-note pool per `Vault` instance.

### Palace — `ompa/palace.py`
Memory-palace metaphor over the vault. Wings (top-level areas) contain
rooms (sub-topics) that link drawers (notes) and halls (freeform facts).
Single `palace.json` file, atomic writes via `.tmp` + rename, collapsed
into a single write via the `batch()` context manager when bulk-mutating.

### Knowledge Graph — `ompa/knowledge_graph.py`
SQLite-backed triple store with temporal validity (`valid_from` /
`valid_to`). WAL mode + composite indexes make `query_entity` and
`query_relation` fast on million-triple graphs. `populate_from_vault`
does bulk `executemany` under one connection; per-note repopulation
deletes stale triples before re-inserting.

### Semantic Index — `ompa/semantic.py`
Sentence-transformers embeddings stored as a single float32 `.npy` matrix
with a sidecar JSON for chunk metadata. L2-normalized at index time so
cosine similarity reduces to a dot product; `argpartition` picks top-K
before Python touches the list. Optional `hnswlib` backend hook for
large vaults. Lazy-loads the model on first use (no import-time cost).

### Classifier — `ompa/classifier.py`
Fourteen message types (`decision`, `incident`, `win`, ...) matched by
precompiled regex. Routes messages to the right folder + hook action
with a confidence score.

## Hooks — `ompa/hooks.py`
Lifecycle integration points:

- `session_start` — builds ~2K-token context (vault listing, North Star,
  active work, palace wings, KG stats) for the agent.
- `user_message` — classifies + injects routing hints.
- `post_tool` — runs after the agent writes a file; auto-updates vault
  cache, palace, KG, and semantic index.
- `pre_compact` — injects key context before context-compaction.
- `stop` — wrap-up checklist.

## MCP Server — `ompa/mcp_server.py`
stdio JSON-RPC exposing 19 tools. Caches `Ompa` instances per
`(vault_path, shared_path, personal_path, enable_semantic)` key so the
sentence-transformers model loads once per process. Path traversal
hardened via `_validate_vault_path` (system-root rejection + `..`
rejection + absolute-path resolution).

## Dual-vault mode
Shared vault (team-visible) + personal vault (agent-private). Writes
auto-classify based on tags, content, and path under
`IsolationMode.STRICT`; MANUAL defers to the caller. Exports from
personal → shared run through `_sanitize_content` which redacts
secrets (OpenAI, AWS, GCP, Azure, Slack, GitHub, Stripe, MongoDB).

## Data flow: a single write
1. Agent calls `ao_write(content, file_path, tags)`.
2. Classifier picks folder if `file_path` not given.
3. Dual-vault config picks shared vs personal vault.
4. Content is sanitized for secrets if going to shared.
5. Note saved to disk (atomic via python-frontmatter).
6. Vault cache invalidated for that path.
7. KG `populate_from_note` deletes stale triples and re-adds fresh.
8. Semantic index re-chunks + re-embeds the file only.
9. Palace `auto_build_from_vault` adds a drawer if the file is in a
   known subtree (`brain/`, `work/active/`, `org/people/`).
