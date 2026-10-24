# Changelog

All notable changes to OMPA are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Changed
- Documentation URL in package metadata now points to the docs site (https://jmiaie.github.io/ompa/) now that GitHub Pages is live. PyPI will show it from the next release; 1.1.0 on PyPI still links to the repo `docs/` folder.
- README: added a docs-site badge.

---

## [1.1.0] — 2026-10-24

First PyPI release built from this repository (`jmiaie/ompa`); 1.0.4–1.0.8 on PyPI were built from the
`ompa/` folder of `jmiaie/MicapAiLabs`. Everything below ships in 1.1.0. The public API is backwards
compatible; new features are opt-in except content keyword search, which replaces file-name matching
when `ompa[semantic]` is not installed.

Measured results (all in `benchmarks/README.md`, with method, machine and caveats):
- Search is 8–24× faster at 1k–50k notes (hybrid p50 17.4 → 2.1 ms at 1k, 185 → 9.4 ms at 10k, 1,061 → 44 ms at 50k; synthetic corpus, stub embedder).
- Temporal KG point-in-time accuracy 0.895 → 1.000 (20-seed Monte Carlo).
- LongMemEval-S (n=470) R@5: hybrid 0.979, keyword-only 0.970 (no ML dependencies).
- Audit tamper Monte Carlo: 24,998/24,998 effective tampers detected unsigned and 25,000/25,000 with HMAC, 0 false positives. A full consistent chain rewrite by an attacker without a key is detected only against an externally anchored head (1,500/1,500 with an anchor, 0/1,500 without).
- Python classifiers now list 3.10–3.13, the versions CI tests.

### Added

- **Tamper-evident audit memory (opt-in, stdlib-only).** See `docs/AUDIT.md`.
  - `ompa.audit.AuditLog`: append-only, hash-chained log in `.palace/audit.sqlite3`. It records every memory mutation: note write/edit/delete/observed, fact add/merge/invalidate/delete, forget, retention, policy and ACL changes, and denied attempts. Entries are canonical JSON with prev-hash + SHA-256 and salted body commitments. RFC 6962 Merkle checkpoints are taken every 1000 entries, and a head file detects truncation.
  - Optional signing: Ed25519 (`pip install ompa[audit]`) or HMAC-SHA256 (stdlib). Keys come from `ao audit keygen`, `audit_key=` or `$OMPA_AUDIT_KEY_FILE`.
  - `verify` detects any edit, deletion, reorder, insertion, truncation or checkpoint tamper, plus note drift (files edited outside OMPA). It is available as `Ompa.audit_verify()`, `ao audit verify [--key] [--anchor]` (exit 1 on failure) and the MCP tool `ao_audit_verify`.
  - Evidence bundles (JSON, sealed, with optional Merkle inclusion proofs): `ao audit export` / `ao audit verify-bundle`, MCP `ao_audit_export`.
- **Provenance and citations.** `SearchResult.citation` and `Triple.citation` give the source path, line and byte span, `content_sha256`, author/agent id, recorded time, valid-from/to, staleness and whether the note matches its audited hash. `search(citations=False)` opts out. The KG gains nullable `agent`, `source_line` and `source_sha256` columns (added in place).
- **Point-in-time view.** `Ompa.as_of(t)` / `ao audit as-of` / MCP `ao_as_of` replay the audit chain up to `t` ("what did the agent know at time T"), bitemporally via `valid_at`.
- **Data policies.**
  - Retention TTL rules by tag, folder, fact predicate or the whole vault: `add_retention_rule`, `apply_retention`, `ao retention …`.
  - Legal holds by subject, path or tag block delete, overwrite, expiry and erasure: `add_legal_hold`, `ao hold …`.
  - Right-to-erasure `forget(subject, aliases)` / `ao forget` / MCP `ao_forget` (dry run by default). It deletes or redacts note text, KG facts, index entries and audit bodies, and vacuums every database. A tombstone with a nonce-salted subject commitment stays in the chain.
- **Vault access control.** `.palace/acl.json` grants read/write/admin to principals, teams or `*`, per vault (private vs shared). Checks are enforced in the `Ompa` API, the KG and the MCP dispatcher (`principal` argument, pinnable via `$OMPA_PRINCIPAL`). Every denial is written to the audit chain, even when the surrounding transaction rolls back. A malformed ACL fails closed.
- New methods `Ompa.delete_note()`, `enable_audit()`, `audit_head()`, `audit_export()`, `set_acl()`, `policies()` and `remove_policy()`. New `ao audit enable` migrates an existing vault (baseline snapshot).
- `benchmarks/audit_overhead.py` (write latency, verify at 10k/100k, citation cost) and `benchmarks/audit_tamper_mc.py` (randomized tamper Monte Carlo). Tamper, erasure-grep, ACL, concurrency and Hypothesis property tests are in `tests/test_audit*.py`.

- **Keyword search over note content, no extra required.** `ompa.keyword.KeywordIndex` is a BM25 index built on the SQLite FTS5 extension that ships with Python, stored in `.palace/keyword_index.sqlite3`. Before this, `Ompa.search()` without `ompa[semantic]` matched only *file names*. It now ranks notes by their content. The index stays fresh the same way the semantic index does: notes are indexed on `write()`, and an `[mtime_ns, size]` refresh catches edits made outside OMPA. Query text is quoted token by token, so FTS5 syntax in a query can't cause an error.
- **Hybrid search.** With `ompa[semantic]`, `search(hybrid=True)` (the default) fuses the semantic and BM25 rankings with reciprocal rank fusion (k=60). `hybrid=False` gives pure semantic search.
- **Reproducible benchmarks**, each documented in `benchmarks/README.md` (method, machine, results):
  - `benchmarks/longmemeval_s.py`: full LongMemEval-S, n=470, bootstrap CIs.
  - `benchmarks/search_latency.py`: 1k/10k/50k notes.
  - `benchmarks/temporal_mc.py`: 20-seed temporal KG Monte Carlo.
- **Why OMPA vs raw Obsidian?** consulting one-pager (`docs/why-ompa.md`) — agent-memory wedge, dual-vault hygiene, Locus + CognitionOS companion stack.
- README + docs home cross-links to [Locus](https://github.com/jmiaie/locus) (vectorless retrieval) and [CognitionOS](https://github.com/jmiaie/cognition-os) (compliance productization).

### Changed

- README: MCP tool table corrected (24 tools; removed the non-existent `ao_stop`), CLI reference lists the new `audit`/`forget`/`retention`/`hold`/`acl` commands (40 commands in total).
- **Resident, compact semantic index.** The index loads once into a single matrix: float16 on disk, float32 in memory. A query is one SIMD matrix-vector pass, replacing a Python loop over every chunk. Search at 50k notes went from 1.06 s to 44 ms (hybrid) or 3 ms (semantic only).
- **New on-disk format.** The index is now `index_meta.json` + `index_vectors.f16`, replacing the JSON-float `semantic_index.json`. At 50k notes that is 411 MB → 60 MB, and a fresh-process cold start went from 6.8 s to 0.5 s.
- **Incremental updates.** Adding or removing a note costs O(that note's chunks), with no full-matrix copies.
- **Automatic migration.** An existing v1 `semantic_index.json` is converted on load, rewritten in the new format on the next save, and then deleted.
- Bulk (re)indexing embeds in batches. It uses the backend's `encode_batch()` if present, or sentence-transformers' batched `encode`.
- The SentenceTransformer model loads once per process and model name, not once per `Ompa`/`SemanticIndex`.
- Semantic search falls back to pure Python when numpy is missing, for custom or NIM backends.
- **Score scale changed.** Hybrid `SearchResult.score` is now an RRF score (≈0.01–0.035), and keyword results score `-bm25`. Only compare scores within one mode. `ao search` now prints 4 significant digits.
- Note-name matching is now only a last resort, used when content search finds nothing.
- `ao rebuild-index` / `rebuild_index()` rebuild the keyword index too.
- rsync and S3 sync skip `keyword_index.sqlite3*`, like `semantic_index/`.
- **README/docs:** removed the "96.6% R@5 on LongMemEval" claim. That is MemPalace's figure for a different pipeline and was never measured on OMPA. The README now publishes OMPA's own measured numbers: hybrid R@5 0.979 [0.964, 0.991] and keyword-only 0.970 on LongMemEval-S (n=470). Also:
  - Removed the stale "MCP 15 tools" badge.
  - Corrected counts to 19 MCP tools and 23 CLI commands.
  - Marked competitor comparison columns as not re-verified.
- `benchmarks/longmemeval.py` (20 hand-written items) is now `benchmarks/smoke_sample.py`, clearly labelled as a smoke test.
- Pin `ruff==0.11.13` in `pyproject.toml` optional `dev` deps; per-file ignore `B008` on `ompa/cli.py` (typer.Option defaults).
- Apply `ruff format` + autofix/`B904`/`E741`/`F841`/`SIM102` cleanups across `ompa/` so CI Lint & Format can go green (pairs with PR #12 CI pin/split).

### Fixed

- **Temporal KG history (#16).** Re-asserting a fact no longer overwrites its earlier validity intervals. In the 20-seed Monte Carlo, point-in-time accuracy went from 0.895 to 1.000 and the share of intervals lost from 36% to 0%.
- **Index freshness (#16).** New and edited notes become searchable without `rebuild_index()`.
- Property test `test_brain_note_roundtrip`: assert against strip-on-read content so Hypothesis examples with leading/trailing spaces do not flake (`Note.from_file` normalizes with `str.strip()`).

---

## [1.0.8] — 2026-05-09

### Fixed

- Sync git history with PyPI: versions 1.0.4–1.0.8 were published manually without corresponding git tags; this commit restores alignment.
- `fix: annotation NameError on Python 3.10–3.13` — forward-reference in type annotations caused `NameError` on Python 3.10/3.11; resolved by quoting annotations or using `from __future__ import annotations`.
- pip-audit added to CI security job for ongoing dependency vulnerability scanning.

---

## [1.0.0] — 2026-05-07

First stable release. Semver commitment begins here — no breaking public API changes without a major version bump.

### Added

- **`ao doctor` command**: rich health check table — vault structure, KG, palace, semantic index, orphans
- **MkDocs Material documentation site** (`docs/`) deployed to GitHub Pages; guides for hooks, MCP, dual-vault, message types; full API reference
- **LongMemEval benchmark** (`benchmarks/longmemeval.py`) — reproducible R@5 measurement with 20-item built-in dataset
- **Framework adapters** (`ompa/adapters/`):
  - `OmpaMemory` + `OmpaRetriever` — LangChain `BaseChatMemory`-compatible components (`ompa[langchain]`)
  - `OmpaReader` + `OmpaVaultRetriever` — LlamaIndex reader + retriever (`ompa[llamaindex]`)
  - `OmpaAgentHooks` — OpenAI Agents SDK `AgentHooks` integration
  - `NIMEmbeddingBackend` — NVIDIA NIM API embeddings, drop-in for sentence-transformers (`ompa[nim]`)
  - `FAISSSemanticIndex` — sub-millisecond ANN search via FAISS flat/IVF index (`ompa[faiss]`)
- **`AsyncOmpa`** (`ompa/async_api.py`): full async-native API backed by `ThreadPoolExecutor`; `async with AsyncOmpa(...) as ao:` context manager; safe for concurrent multi-agent workloads
- **Token counting** (`ompa/token_counter.py`): tiktoken-precise counting with word-count heuristic fallback; `tokens_hint` in `HookResult` is now accurate (`ompa[tiktoken]`)
- **Pluggable embedding backend**: `SemanticIndex` and `Ompa` accept `embedding_backend=` — swap sentence-transformers for NIM or any `encode(text) -> list[float]` implementation
- **Multi-node vault sync** (`ompa/sync/`):
  - `SyncBackend` ABC + `SyncResult` dataclass
  - `GitSyncBackend` — add → commit → push / pull --rebase
  - `S3SyncBackend` — S3/R2/MinIO sync via boto3 (`ompa[s3]`)
  - `RsyncBackend` — rsync over SSH, Tailscale-ready (excludes semantic index)
  - `ao sync --backend git|s3|rsync --remote <target>` CLI option
- **Vault migration tooling** (`ompa/migration.py`): `VaultMigrator.check()` + `.run()` with dry-run; schema versioned via `.palace/schema_version`; three migrations: init palace, composite KG indexes, WAL mode
- **`ao migrate-vault` CLI command** with `--dry-run` and `--force` flags
- **Property-based tests** (`tests/test_properties.py`): 15 hypothesis-driven invariant tests across KG, classifier, token counter, vault, and sync
- **`STABILITY.md`**: documents the stable public API contract and deprecation policy
- Optional dep groups: `ompa[langchain]`, `ompa[llamaindex]`, `ompa[nim]`, `ompa[tiktoken]`, `ompa[s3]`, `ompa[faiss]`, `ompa[docs]`
- `ompa[all]` now includes `ompa[semantic]` + `ompa[tiktoken]`
- GitHub scaffolding: issue templates, PR template, `CONTRIBUTING.md`
- `.markdownlint.json`: project-wide markdown lint config
- CI: `Security Audit` job (bandit + pip-audit) on every push; `Deploy Docs` workflow for GitHub Pages

### Changed

- `__version__` → `1.0.0`; `Development Status` classifier → `Production/Stable`
- `KnowledgeGraph`: WAL mode + `PRAGMA synchronous=NORMAL`; thread-local connection cache; three new composite indexes (`subject_date`, `object_pred`, `validity`)
- `SemanticIndex` cosine similarity moved to pure numpy — removes `sentence_transformers.util` from hot path
- `ao sync` CLI: added `--backend`, `--remote`, `--message`, `--push` options
- `hooks.py`: `tokens_hint` computed via `count_tokens()` (tiktoken when available, heuristic otherwise)
- `pyproject.toml`: `Documentation` URL → `https://jmiaie.github.io/ompa`; `mypy`, `hypothesis`, `bandit`, `pip-audit` added to dev deps

### Security

- All 26 source files pass mypy with 0 errors
- bandit SAST: clean (exit 0, no medium/high findings)
- pip-audit added to CI for ongoing dependency vulnerability scanning
- See `SECURITY_AUDIT.md` for full report

---

## [0.4.2] — 2026-05-07

### Added

- Python 3.14 classifier and CI matrix support
- `pytest-cov` dev dependency for coverage reporting
- Codecov integration in CI workflow
- `mypy` dev dependency for type checking
- `[tool.ruff]`, `[tool.pytest.ini_options]`, `[tool.mypy]` config sections in `pyproject.toml`

### Changed

- CI workflow: added `fail-fast: false`, pip caching, explicit job names, and `--tb=short`
- CI lint job: replaced `black --check` with `ruff format --check`
- README: complete restructure — hero section, 8 badges, comparison table above the fold, CLI reference table, dual-vault section

### Removed

- Internal build/prompt files removed from public repo root (`CLAUDE_CODE_PROMPT.md`, `AGNOSTIC_OBSIDIAN_CLAUDE_CODE_PROMPT.md`, `PUSH.md`, `RELEASE.md`)

---

## [0.4.1] — 2026-04-11

### Fixed

- **Path traversal hardening**: all vault file operations now resolve and boundary-check paths against the vault root before proceeding
- **Semantic lazy-init**: `SemanticIndex._model` deferred until first access — import no longer triggers model download
- Brain note names with path separators now rejected at the API boundary

### Security

- Full security audit completed: 8/10 rating, 2 low-severity bandit findings (acceptable subprocess usage for git)
- 59/59 unit tests passing at audit time

---

## [0.4.0] — 2026-04-10

### Added

- **Dual-vault architecture**: `DualVaultConfig` + `IsolationMode` for shared/personal vault separation
- `IsolationMode.AUTO` — content auto-classified to shared or personal vault based on message type
- `IsolationMode.MANUAL` — explicit `VaultTarget` routing per operation
- Export/import between vaults with sanitization (strips personal identifiers before sharing)
- `ao_dual_vault_*` MCP tools for shared vault operations
- `DualVaultConfig`, `IsolationMode`, `VaultTarget` exported from public API

### Changed

- `Ompa` constructor accepts either `vault_path: str` or `config: DualVaultConfig`

---

## [0.3.1] — 2026-04-09

### Fixed

- Orphan detection: notes with broken wikilinks now correctly flagged
- Brain note counting: `ao status` count was off-by-one in nested structures
- Wikilink resolution: `[[Note Name]]` now resolves case-insensitively across the vault

---

## [0.3.0] — 2026-04-08

### Added

- **Auto-populate KG from vault**: `KnowledgeGraph.populate_from_vault()` scans existing notes, extracts entities from frontmatter tags, folder paths, and `[[wikilinks]]`
- **Incremental semantic index**: `SemanticIndex` tracks file modification times and only re-embeds changed notes
- **Brain note sync**: `ao session-start` now syncs the brain note list against actual vault contents

### Changed

- `ao rebuild-index` now skips unchanged files (was full rebuild previously)

---

## [0.2.2] — 2026-04-07

### Security

- Resolved all `bandit` scan issues: B110 (bare except), B404 (subprocess import), B603 (subprocess call)
- Added `usedforsecurity=False` to MD5 hash usage

### Changed

- PyPI publishing switched to **trusted publishing** (OIDC) — no more long-lived tokens

---

## [0.2.1] — 2026-04-06

### Added

- Expanded test suite: 59 test cases across 10 test classes
- `TestDualVault` with 18 test cases covering shared/personal isolation
- `TestKGPopulation` with 5 test cases for vault-to-KG auto-population
- `TestOrphanAndBrainFixes` with 6 regression test cases

### Fixed

- Reliability fixes for vault note CRUD under concurrent access
- `ao orphans` false-positive rate reduced by improved wikilink normalization
- UTF-8 encoding explicitly enforced for all vault file writes

### Security

- Path traversal protection added to all `vault.py` file operations

---

## [0.2.0] — 2026-04-05

### Changed

- **Complete rename**: `AgnosticObsidian` → `Ompa`, `agnostic_obsidian` → `ompa` throughout
- Backward compatibility alias: `AgnosticObsidian = Ompa` preserved in `__init__.py`
- Package published to PyPI as `ompa`

### Added

- Asciinema demo recording (`demo.cast`)
- PyPI version, Python version, and License shields in README
- `SECURITY_AUDIT.md`

### Fixed

- All ruff lint errors resolved
- All black formatting issues resolved

---

## [0.1.2] — 2026-04-04

### Fixed

- Import resolution: all internal imports updated from `agnostic_obsidian` to `ompa`
- CI: workflow now triggers correctly on `v*` version tags
- Build: setuptools package discovery config added

---

## [0.1.1] — 2026-04-03

### Changed

- Rebrand: **AgnosticObsidian → OMPA** (Obsidian-MemPalace-Agnostic)
- Credits section added to README acknowledging MemPalace, obsidian-mind, Claude Code, OpenClaw

---

## [0.1.0] — 2026-04-02

### Added

- Initial release as **AgnosticObsidian**
- **Three-layer memory architecture**: Vault (markdown) + Palace (metadata) + Knowledge Graph (SQLite)
- **5 lifecycle hooks**: `session_start`, `user_message`, `post_tool`, `pre_compact`, `stop`
- **15 message types** with auto-routing: DECISION, INCIDENT, WIN, LOSS, BLOCKER, QUESTION, SUGGESTION, REVIEW, BUG, FEATURE, LEARN, RETROSPECTIVE, ALERT, STATUS, CHORE
- **MCP server** with 14 tools via Model Context Protocol
- **CLI** with 14 commands (`ao init`, `ao session-start`, `ao classify`, etc.)
- **Local semantic search** using `sentence-transformers` (all-MiniLM-L6-v2), zero API cost
- **Temporal knowledge graph**: SQLite triples with validity windows, `query_entity`, `timeline`
- **Palace navigation**: wings, rooms, drawers, halls, tunnels
- GitHub Actions CI/CD with matrix testing (Python 3.10–3.13)

[Unreleased]: https://github.com/jmiaie/ompa/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/jmiaie/ompa/compare/v0.4.2...v1.0.0
[0.4.2]: https://github.com/jmiaie/ompa/compare/v0.4.1...v0.4.2
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
