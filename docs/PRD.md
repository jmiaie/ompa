# PRD: OMPA + Locus memory bundle

**Tagline:** memory you can audit, retrieval you can explain, no GPU.

| | |
|---|---|
| Owner | jmiaie (decisions marked **[Owner]**) |
| Status | Draft for review |
| Scope | OMPA as the canonical memory package of a combined "ompa + locus" MCP bundle |
| Related | [ROADMAP](ROADMAP.md) · [kg-schema-v1](kg-schema-v1.md) · [Why OMPA](why-ompa.md) · [STABILITY](../STABILITY.md) |

Conventions in this document. **Fact** means it is true of the code on `main` today, with a file reference.
**Assumption** means it has not been checked against users or other repos. No usage numbers, customers or
benchmark results are claimed here, because the repo has none to cite.

---

## 1. Problem

Coding and chat agents start each session without memory. The common fixes have three costs:

1. **You cannot audit them.** Memory lives in a vector store or in an LLM's summary. A human cannot open it, read
   it, correct it or diff it.
2. **You cannot explain retrieval.** An embedding hit gives no reason why a passage was returned.
3. **They need infrastructure.** That means a GPU, a hosted vector database, or a paid embedding API per query.

OMPA already addresses (1) and (3) for storage. Locus addresses (2) for retrieval. Today each is installed and set up
separately, and nothing defines how they share data.

## 2. Product shape

| Layer | Package | Owns | Does not own |
|---|---|---|---|
| Memory (source of truth) | `ompa` (PyPI) | Markdown vault, palace metadata, temporal knowledge graph, lifecycle hooks, message classification, writes | Ranking explanations |
| Retrieval | `locus-rag` (PyPI, repo jmiaie/locus) | BM25 + KG + link-walking retrieval with explanations | Writing memory |
| Contract between them | [`docs/kg-schema-v1.md`](kg-schema-v1.md), `ompa/kg_schema.py` | Triple table shape, provenance, versioning | — |

Rule: **OMPA writes, Locus reads.** Locus, pliamem and AegisFlow treat OMPA's vault and KG as read-only input.

Facts about OMPA today:

* Vault of plain markdown with frontmatter and wikilinks (`ompa/vault.py`). Humans can read and edit it in any editor.
* Temporal KG in one SQLite file per vault, `<vault>/.palace/knowledge_graph.sqlite3`, with validity windows
  (`valid_from`, `valid_to`) and a `source_file` provenance column (`ompa/knowledge_graph.py`).
* MCP server over stdio JSON-RPC, entry point `ao-mcp` / `python -m ompa.mcp_server` (`ompa/mcp_server.py`).
  It registers **19 tools** in `TOOLS`. The README and STABILITY.md say 15, and CLAUDE.md says 14 (see open questions).
* Core install depends only on `typer`, `rich` and `python-frontmatter`. Semantic search is an optional extra
  (`ompa[semantic]`, sentence-transformers on CPU). Without it, search falls back to keyword matching
  (`ompa/semantic.py`). No GPU is required in either mode.

Locus facts are taken from OMPA's README only: "Vectorless RAG — BM25 + KG + link walking, zero GPU". This PRD does not
check Locus's code. **Assumption:** Locus can read a SQLite triple table and a markdown folder.

## 3. Target users

| Segment | Why they care | Evidence level |
|---|---|---|
| Developers running Claude Code, Cursor, Windsurf or other MCP clients who want memory across sessions | One install, local files, no account | Assumption (MCP clients are the documented integration path in `docs/guides/mcp.md`) |
| Teams in regulated settings who must show where an agent's answer came from | Markdown + provenance column + explainable retrieval | Assumption; CognitionOS (README "Companion stack") targets this |
| Python agent builders (LangChain, LlamaIndex, OpenAI Agents SDK) | Library API and adapters in `ompa/adapters/` | Fact that adapters exist; demand is an assumption |

Non-users: teams that need hosted multi-tenant memory with a web UI, or GPU-scale vector search over millions of
documents.

## 4. Jobs to be done

1. "When I start an agent session, give it the decisions and active work from last time, within a small token budget."
2. "When the agent recalls something, show me the note and the fact it came from, so I can check or fix it."
3. "When a fact stops being true, keep the history but stop serving it as current."
4. "Let me install all of this from my MCP client's server list, without running a database or a GPU."

## 5. Goals and non-goals

**Goals (this bundle)**

* G1. One documented, versioned KG contract that OMPA owns and readers can test against. *Done in this PR as v1.*
* G2. OMPA can be listed in the official MCP Registry, Smithery and PulseMCP, pointing at the existing PyPI package.
* G3. A user can install OMPA + Locus together and get an explained answer with sources from their own vault.
* G4. Benchmark claims are honest: every published number says who measured it and on what data.
* G5. Repeated MCP tool calls stay fast. The embedding model loads once per vault per server process.

**Non-goals**

* Hosted service, accounts or billing.
* Replacing Locus's ranking inside OMPA, or adding a vector database.
* Writing to the KG from Locus, pliamem or AegisFlow.
* Summarizing memory. Verbatim storage stays.

## 6. User stories and acceptance criteria

**US1. Registry install.** As an MCP client user, I can add OMPA from the MCP Registry.
* `server.json` at the repo root validates against the registry schema and names PyPI package `ompa` at the version in
  `pyproject.toml`. *Checked by `tests/test_distribution.py` for version and entry-point consistency. Schema
  validation needs the registry's `mcp-publisher` tool, which was not run.*
* The README contains `mcp-name: io.github.jmiaie/ompa`, which PyPI ownership verification requires.
* Setting `OMPA_VAULT_PATH` points the server at a vault without passing `vault_path` on every call.
  *Done in this PR. `docs/guides/mcp.md` already documented this variable, but the code did not read it.*

**US2. Auditable facts.** As a reviewer, for any fact the agent cites, I can find the note it came from.
* Every triple created from a note has `source_file` set to that note's path. *Checked by
  `tests/test_kg_contract.py`.*
* `source_file` is vault-relative and the same on every machine. *Not true today: it is absolute or CWD-relative.
  Planned for kg-schema v2.*

**US3. Point-in-time recall.** As an agent, I can ask what was true on a given date.
* `ao_kg_query` with `as_of` returns only facts whose validity window contains that date. *Fact:
  `KnowledgeGraph.query_entity`.*

**US4. Explained retrieval over OMPA memory.** As a user, I ask Locus a question and get passages from my OMPA vault
with the reason each was chosen (term match, KG edge, link hop).
* Locus reads the OMPA KG through the v1 contract and refuses an unknown `user_version`.
* *Not done. This depends on Locus work; see ROADMAP "Next".*

**US5. Contract safety.** As a maintainer of a reader project, I learn about breaking KG changes before they ship.
* `PRAGMA user_version` is stamped (done). Breaking changes follow the rules in kg-schema-v1 section 9. Contract test in
  CI (done).

## 7. Functional requirements

| ID | Requirement | Status |
|---|---|---|
| F1 | KG stores triples with validity windows, confidence and provenance | Exists |
| F2 | KG version stamped in `PRAGMA user_version`; checker `ompa.kg_schema.check_kg_schema()` | This PR |
| F3 | MCP stdio server, entry point `ao-mcp` | Exists |
| F4 | `server.json` registry manifest | This PR (fields to confirm, see PR) |
| F5 | `OMPA_VAULT_PATH` default vault for MCP | This PR |
| F6 | Per-vault instance cache in the MCP server | This PR |
| F7 | Vault-relative `source_file` | Roadmap (kg-schema v2) |
| F8 | Bundle install path (`ompa` + `locus-rag`) documented end to end | Roadmap |
| F9 | Smithery and PulseMCP listings | Roadmap |

## 8. Non-functional requirements

* **No GPU, no network at query time.** Core install has no ML dependencies. The semantic extra runs on CPU.
  The first semantic use downloads the `all-MiniLM-L6-v2` model unless it is cached; document this for offline users.
* **Local data.** All state lives under the vault directory. Nothing leaves the machine unless a sync backend is set up.
* **Backward compatibility.** STABILITY.md semver rules apply. KG readers follow kg-schema-v1 section 8.
* **Latency.** No measured targets exist yet. **[Owner]** pick targets after the benchmark work in the roadmap.
  This PR removes the per-call model reload, which was the obvious cost.
* **Security.** The MCP server rejects `vault_path` values containing `..` or a filesystem root (existing behaviour,
  now also applied to `OMPA_VAULT_PATH`). Bandit runs in CI.

## 9. Distribution

| Channel | What it needs | Status |
|---|---|---|
| PyPI | `ompa` 1.0.8 is published (CHANGELOG) | Exists |
| Official MCP Registry (registry.modelcontextprotocol.io) | `server.json`; README `mcp-name` marker; `mcp-publisher login github` as jmiaie; `mcp-publisher publish` | Manifest in this PR. Publishing is an owner action. The marker must ship in a PyPI release before publishing, because the registry reads the PyPI description. |
| Smithery | Smithery lists servers from a repo config (`smithery.yaml`) or a hosted endpoint. OMPA is stdio and local-file based, so a local stdio listing fits. | Not started. **[Owner]** confirm Smithery's current format; do not host user vaults. |
| PulseMCP | Directory that lists servers (it also ingests the official registry). | Not started. Expected to follow from the registry listing; submit manually if it does not. **Assumption.** |

Bundle positioning in every listing: "OMPA is the memory layer; pair with Locus for explained retrieval." Use the same
tagline everywhere.

## 10. Pricing and positioning

* OMPA and Locus are MIT-licensed and free. This PRD proposes no pricing for them.
* Commercial value, if any, sits in CognitionOS (audit, RBAC, retention, PHI redaction per the README), which is
  outside this PRD. **[Owner]**
* Position against vector-store memory on three points we can show in code: readable markdown, provenance per fact,
  and no GPU. Do not position on recall numbers until OMPA has its own published run (G4).

## 11. Success metrics

No baselines exist. These are the metrics to start collecting, not targets.

* Registry: listing live on the MCP Registry, Smithery and PulseMCP (yes or no).
* Adoption: PyPI downloads for `ompa` and `locus-rag` (public via pypistats), GitHub stars and issues.
* Contract health: number of reader projects that run `check_kg_schema` in their CI (goal: locus first).
* Quality: OMPA's own LongMemEval run on the full public dataset, published with its exact command.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Overclaiming benchmarks damages trust | README now attributes 96.6% to MemPalace and describes what OMPA's script measures (this PR) |
| Readers depend on unstable details (absolute `source_file`, indexes) | kg-schema-v1 marks them; v2 plan for `source_file` |
| Tool-count and env-var docs drift from code (15 vs 19 tools; `OMPA_ENABLE_SEMANTIC`, `OMPA_AGENT_NAME` documented but not read) | Fix docs or implement; add a test like `tests/test_distribution.py` |
| Registry manifest format changes | Fields marked unverified in the PR; validate with `mcp-publisher` before publishing |
| Cached MCP instances serve a stale semantic index after an external rebuild | Cache reloads the index when its file mtime changes (tested) |
| Two packages to install confuses users | One documented bundle command (roadmap) |

## 13. Open questions for the owner

1. Is "OMPA writes, Locus reads" final? Does Locus ever need to write derived edges back, for example into a separate
   table that OMPA never reads?
2. Bundle packaging: a meta-package (for example `ompa[locus]` extra), two separate registry entries, or one MCP
   server that serves both tool sets?
3. Registry launch command: keep `uvx --from ompa ao-mcp` (as in `server.json`), or add a console script named
   `ompa` that starts the MCP server, so the standard `uvx ompa` works?
4. Should `OMPA_ENABLE_SEMANTIC` and `OMPA_AGENT_NAME` be implemented or removed from `docs/guides/mcp.md`?
5. Which tool count is canonical for docs and badges? Code registers 19.
6. pliamem and AegisFlow: who owns adding the contract check on their side?
7. Is the MemPalace link in the README (`corbt/mem_palace`) the right source for the 96.6% figure?
