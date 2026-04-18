# Changelog

All notable changes to OMPA are documented in this file.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.5.0] - Unreleased

### Added
- Vault note cache with mtime-keyed invalidation — `list_notes`, `find_orphans`,
  `get_stats`, and `search_by_name` share a single parsed-note cache instead of
  re-globbing and re-parsing on every call.
- MCP server Ompa instance cache — `_get_ompa()` reuses a single `Ompa` per
  vault configuration across tool calls, avoiding repeated model loads.
- Palace `batch()` context manager — defers `_save()` calls until exit, so
  `auto_build_from_vault` and `sync()` write the JSON once instead of per-room.
- Semantic index v2 binary storage: embeddings stored as `.npy` float32 matrix
  with a sidecar `.meta.json` for chunk metadata. Legacy single-JSON format is
  auto-migrated on first load.
- Batched `model.encode()` in `index_file()` — one forward pass per file instead
  of one per chunk.
- Best-per-path dedup in semantic search — returns up to `limit` distinct files
  instead of multiple results from the same file.
- KG `populate_from_vault` uses a single SQLite connection with `executemany`
  instead of one transaction per note.
- Stale-triple cleanup on KG re-populate — old `links_to`, `has_tag`,
  `in_folder`, `in_subfolder` triples are deleted before re-adding.
- `tokens_hint` renamed internally to word-count-based estimate (`len(output) // 4`).
- Shared `extract_wikilinks()` function in `vault.py` — reused by both
  `Note._extract_wikilinks` and `knowledge_graph.populate_from_note`.
- Word-boundary regex matching for config indicators — prevents false positives
  like `desk-work` matching `sk-` or `tokenize` matching `token`.
- MCP dispatch registry — replaces 18-arm if/elif chain with data-driven
  `_DISPATCH` dict. Exceptions logged server-side; callers get sanitized errors.
- Version sourced from `importlib.metadata` at runtime — `__init__.py` and
  `mcp_server.py` no longer hardcode version strings.
- Docstring polish on all public `stats()`, `get_stats()`, `sync()`, and
  `validate_write()` methods with explicit return-type documentation.
- Expanded ruff ruleset (`I`/`B`/`UP`/`SIM`/`RUF`/`C4`/`PIE`) with targeted
  per-file ignores. All ~114 resulting violations fixed: `ClassVar`
  annotations on class-level collections, `contextlib.suppress` for
  ignorable errors, `raise ... from e` chains, `is True` identity checks.
- `pytest-cov` wired in with branch coverage — 70% baseline across `ompa/`
  (CLI + `__main__` excluded).
- 13 `Note.save → Note.from_file` round-trip invariance tests covering
  unicode, wikilink normalization, body fence-lookalikes, and idempotence.
- `lru_cache` on `KnowledgeGraph._entity_id` / `_triple_id` helpers — bulk
  ingest no longer re-hashes the same subject/predicate/object strings.
- Precompiled classifier regex patterns cached per class.
- Mypy baseline config in `pyproject.toml` for gradual tightening.
- `tests/test_ompa.py` (2097 lines, 11 classes) split into nine per-module
  test files matching the package layout.

### Changed
- `search()` filter no longer silently falls back to unfiltered results when a
  wing/room filter matches nothing. Returns the (possibly empty) filtered set.
- Classifier `PERSON_INFO` pattern no longer includes hardcoded first names
  (Sarah, John, etc.). Replaced with generalizable role/context patterns.
- Inline imports hoisted to module scope in `core.py`, `hooks.py`, and
  `knowledge_graph.py`.
- All `X | None` type hints that defaulted to `None` are now correctly annotated
  as `X | None = None` across `core.py`, `cli.py`, `vault.py`, `mcp_server.py`,
  and `knowledge_graph.py`.

### Fixed
- Removed unused `import re` from `knowledge_graph.py` after wikilink extraction
  was consolidated into `vault.py`.

## [0.4.2] - Unreleased

### Fixed
- **Palace data-loss bug**: `create_wing()` and `create_room()` no longer
  overwrite existing rooms/drawers/halls when called on an already-existing
  wing or room. Uses `setdefault()` semantics.
- **KG `query_relation` temporal filter**: now respects `as_of` parameter
  and excludes invalidated triples, consistent with `query_entity`.
- **MCP path-traversal hardening**: vault path validation rejects filesystem
  roots, known system directories (`/etc`, `C:\Windows`, etc.), and `..`
  components pre-resolve.
- **Secret-redaction broadened**: `_sanitize_content` now covers GitHub PATs
  (`ghp_`, `gho_`), Slack tokens (`xox[bpa]-`), Stripe keys (`sk_live_`,
  `pk_live_`), generic JWTs (`eyJ`), GCP private keys, Azure storage keys,
  and MongoDB URIs with credentials.
- **Search filter silent-fallback removed**: `_search_vault` returns empty
  results (not unfiltered fallback) when a wing/room filter matches nothing.

### Changed
- README: corrected message-type list, MCP tool count (19), CLI command
  count (21), and removed nonexistent `ao_stop` tool reference.
- CLAUDE.md: fixed tool count and removed "multilingual" claim from classifier.
- SECURITY_AUDIT.md: bumped to v0.4.1 and noted path-traversal fixes.

## [0.4.1] - 2026-04-12

### Fixed
- Path traversal hardening across vault, brain-note, and MCP operations.
- Semantic search model now lazy-initializes — importing `ompa` no longer
  triggers a multi-hundred-MB model download.

## [0.4.0] - 2026-04-10

### Added
- **Dual-vault architecture**: shared + personal vaults with configurable
  isolation modes (`strict`, `standard`, `manual`).
- Content auto-classification to route writes to the correct vault.
- `ao_write`, `ao_export`, `ao_import` MCP tools for dual-vault operations.
- `write-note`, `export`, `import-note`, `migrate` CLI commands.

## [0.3.1] - 2026-04-10

### Fixed
- Orphan detection now resolves wikilinks by filename (case-insensitive)
  instead of requiring exact path matches.
- Brain note counting includes notes with `wing: brain` in frontmatter.
- Wikilinks with `.md` extension are resolved correctly.

## [0.3.0] - 2026-04-10

### Added
- Auto-populate knowledge graph from vault notes (wikilinks, tags, folders).
- Incremental semantic search index updates on single-file changes.
- Brain note sync: edits auto-update KG and search index.
- `kg-populate`, `sync`, `rebuild-index` CLI commands.

## [0.2.2] - 2026-04-10

### Fixed
- Bandit compliance (B110, B404, B603).
- Brain note names with path separators are rejected.
- Ruff lint and black formatting compliance for CI.

## [0.2.1] - 2026-04-10

### Fixed
- SQLite connection leak — all KG methods use context managers.
- Atomic triple insertion (entity + triple in single transaction).
- SQL-level date filtering in `query_entity`.
- PostToolHook crash (missing `message_type` attribute).
- MCP crash on malformed JSON input.
- `Note.save()` writes UTF-8 explicitly.

### Added
- `python -m ompa` entrypoint via `__main__.py`.
- `py.typed` marker for PEP 561 type checking support.
- Python 3.13 added to CI test matrix.
- MCP vault_path lockdown, search limit cap (100 results).
- Error message sanitization in MCP responses.
- Tests: 21 to 42.

### Removed
- Phantom dependencies: `watchdog`, `chromadb`.
- Subprocess `grep` fallback — replaced with pure Python keyword search.

## [0.2.0] - 2026-04-10

### Changed
- **Rebranded**: AgnosticObsidian to OMPA. Backward-compatible
  `AgnosticObsidian` alias preserved.
- Lightweight core install — `numpy` and `sentence-transformers` moved to
  `[semantic]` extras.
- Removed 2,672 lines of dead code and stale documentation.

### Fixed
- `PostToolHook` crash on missing `message_type` attribute.
- Semantic indexing regex was skipping valid `.md` files.
- `Palace.list_closets()` was overwriting data with empty dict.
- Windows-incompatible `grep` call in vault search.
- MCP server protocol name and version mismatch.
- `arguments.pop()` mutating shared state in MCP handler.

## [0.1.2] - 2026-04-10

### Fixed
- Version bump for PyPI publishing.

## [0.1.1] - 2026-04-10

### Fixed
- Import paths updated from `agnostic_obsidian` to `ompa`.

## [0.1.0] - 2026-04-10

### Added
- Initial release as OMPA (formerly AgnosticObsidian).
- MCP server with 15 tools for Claude Desktop / Cursor / Windsurf.
- Memory Palace: wings, rooms, drawers, halls, tunnels.
- Temporal Knowledge Graph with SQLite backend.
- Semantic search with sentence-transformers embeddings.
- Message classifier (15 types).
- CLI with 14 commands via Typer.
- 21 tests.

[0.5.0]: https://github.com/jmiaie/ompa/compare/v0.4.1...HEAD
[0.4.2]: https://github.com/jmiaie/ompa/compare/v0.4.1...p0-fixes
[0.4.1]: https://github.com/jmiaie/ompa/compare/v0.4.0...v0.4.1
[0.4.0]: https://github.com/jmiaie/ompa/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/jmiaie/ompa/compare/v0.3.0...v0.3.1
[0.3.0]: https://github.com/jmiaie/ompa/compare/v0.2.2...v0.3.0
[0.2.2]: https://github.com/jmiaie/ompa/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/jmiaie/ompa/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/jmiaie/ompa/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/jmiaie/ompa/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/jmiaie/ompa/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/jmiaie/ompa/releases/tag/v0.1.0
