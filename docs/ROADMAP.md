# Roadmap: OMPA as the bundle's memory layer

Companion to [PRD.md](PRD.md). Released history is in [CHANGELOG.md](../CHANGELOG.md), and API guarantees are in
[STABILITY.md](../STABILITY.md). This roadmap does not change either. The repo has no other roadmap or status document.

Each item lists deliverables, an exit criterion and dependencies. "This PR" means branch `claude/growth-memory-bundle`.

## Now (0–30 days)

| # | Deliverable | Exit criterion | Depends on |
|---|---|---|---|
| N1 | KG contract v1: `docs/kg-schema-v1.md`, `ompa/kg_schema.py`, `PRAGMA user_version` stamp | `tests/test_kg_contract.py` green in CI. *This PR.* | — |
| N2 | Honest benchmark wording in README, docs and the benchmark script | No unattributed 96.6% anywhere (`tests/test_distribution.py`). *This PR.* | — |
| N3 | MCP instance cache per vault; `OMPA_VAULT_PATH` support | `tests/test_mcp_cache.py` green; model loads once per vault. *This PR.* | — |
| N4 | `server.json` for the MCP Registry, plus README `mcp-name` marker | Manifest consistent with pyproject (`tests/test_distribution.py`). *This PR.* | — |
| N5 | Release 1.0.9 with N1–N4 | Tag pushed, PyPI page shows the `mcp-name` marker | Owner release |
| N6 | Publish to the MCP Registry | `mcp-publisher publish` succeeds, listing visible | N5, owner GitHub login |
| N7 | Docs drift: tool count (code has 19), `OMPA_ENABLE_SEMANTIC` / `OMPA_AGENT_NAME` documented but unread | Docs match code, or the variables are implemented with tests | Owner decision (PRD Q4, Q5) |

## Next (30–60 days)

| # | Deliverable | Exit criterion | Depends on |
|---|---|---|---|
| X1 | Locus reads OMPA's KG through the v1 contract (vendored `kg_schema.py` or a dependency on `ompa`) | Locus CI builds an OMPA vault and runs `check_kg_schema` | jmiaie/locus maintainers |
| X2 | Bundle install doc: one page, from `pip install` to an explained answer from your own vault | A fresh machine follows it without help | X1 |
| X3 | Smithery and PulseMCP listings | Listings live; install command matches `server.json` | N6; Smithery format check |
| X4 | Own LongMemEval run on the full public dataset, with the exact command and hardware | Number published in README with date and command; sample-only runs labelled as such | Dataset licence check |
| X5 | Contract check in pliamem and AegisFlow | Each repo has an issue or PR adding the check | Their owners |

## Later (60–90 days)

| # | Deliverable | Exit criterion | Depends on |
|---|---|---|---|
| L1 | kg-schema v2: vault-relative POSIX `source_file`; migration step in `ompa/migration.py` | v2 doc, migration with `--dry-run`, readers accept v1 and v2 before OMPA writes v2 | X1, X5 |
| L2 | Decide the registry launch command (`uvx --from ompa ao-mcp` vs an `ompa` console script) | `server.json` updated; registry listing verified with a real client | PRD Q3 |
| L3 | Latency targets for MCP calls on a reference vault | Targets in PRD with a script that measures them | X4 tooling |

## Not planned

Hosted memory service, GPU or vector-database backends in core, and writes to the KG from reader projects.
See PRD non-goals.
