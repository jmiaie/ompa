# MCP Tool Reference

OMPA's MCP server exposes 19 tools over stdio JSON-RPC. All tools accept
`vault_path` (single-vault) or `shared_vault_path` + `personal_vault_path`
(dual-vault) to target a specific vault.

## Setup

```bash
# Claude Code
claude mcp add ompa -- python -m ompa.mcp_server
```

The first call loads sentence-transformers (~300 MB download, ~2 s
warm start after that). Subsequent calls reuse the cached `Ompa`
instance per vault.

## Lifecycle

| Tool | Purpose | Returns |
|---|---|---|
| `ao_session_start` | Load ~2 K tokens of context (vault, North Star, palace) | structured session context |
| `ao_wrap_up` | Run stop-hook checklist | `{ok, checks}` |
| `ao_init` | Create a new vault with the standard folder structure | `{created, paths}` |

## Discovery & search

| Tool | Purpose |
|---|---|
| `ao_search` | Hybrid text + semantic search (falls back to text if semantic disabled) |
| `ao_orphans` | List notes with no incoming wikilinks |
| `ao_status` | Vault stats: note count, folders, KG size |
| `ao_classify` | Classify a single message (decision / incident / win / …) |

## Knowledge graph

| Tool | Purpose |
|---|---|
| `ao_kg_add` | Add a triple: `(subject, predicate, object, valid_from)` |
| `ao_kg_query` | Query triples by subject, with optional `as_of` temporal filter |
| `ao_kg_stats` | Counts of entities + triples |
| `ao_kg_populate` | Bulk-rebuild from vault (wikilinks + tags + folders) |

## Memory palace

| Tool | Purpose |
|---|---|
| `ao_palace_wings` | List wings |
| `ao_palace_rooms` | List rooms in a wing |
| `ao_palace_tunnel` | Create a tunnel (cross-wing association) |

## Writes

| Tool | Purpose |
|---|---|
| `ao_write` | Write content to auto-classified vault + file (dual-vault aware) |
| `ao_export` | Export personal-vault note to shared (with secret redaction) |
| `ao_import` | Import a note from outside into the vault |
| `ao_validate` | Validate a note against schema + frontmatter rules |
| `ao_sync` | Full sync: KG + palace + semantic index |

## Input patterns

`tags` accepts either a JSON array or a comma-separated string:

```json
{"tags": ["decision", "db"]}         // preferred
{"tags": "decision, db"}             // tolerated for CLI-style callers
```

Paths can use forward slashes even on Windows — the server resolves to
OS-native paths internally:

```json
{"file_path": "work/active/auth.md"}  // works on Windows too
```

## Error shape

Every tool returns `{"error": "<message>"}` on failure instead of
raising. Messages are sanitized — the server logs the full traceback
to stderr, the client gets the short form.
