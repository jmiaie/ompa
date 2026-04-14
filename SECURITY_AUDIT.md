# OMPA Security Audit Report

**Date**: 2026-04-14
**Auditor**: Jarv (Tai agent) — refreshed during the v0.4.2 review
**Version**: 0.4.2
**Repository**: https://github.com/jmiaie/ompa

## Executive Summary

OMPA is a **well-designed, secure AI agent memory layer** with minimal attack
surface. The architecture is local-first: the MCP server speaks JSON-RPC over
stdin/stdout, there are no listening sockets, and the only outbound network
traffic is the optional one-time sentence-transformers model download.

Recent hardening (v0.4.1 and v0.4.2) closed several path-traversal edge cases
and broadened credential redaction on vault exports.

**Overall Security Rating**: ✅ **GOOD** (8.5/10)

## Test Results

| Check | Status | Details |
|-------|--------|---------|
| Unit Tests | ✅ 94/94 passed | Covers palace, KG, vault, hooks, classifier, semantic, MCP, dual-vault |
| Bandit Security Scan | ⚠️ 2 Low | Subprocess use for `git log` (intentional) |
| Build | ✅ Success | `pip install -e .` clean |
| Import | ✅ Success | `python -m ompa` / `ao --help` run clean |

Run the scan with:

```bash
pip install bandit
bandit -r ompa/
```

## Security Issues Found

### 🟡 Low Severity (2) — Subprocess Usage for Git Operations

- **Location**: `ompa/hooks.py:94–110`
- **Issue**: Uses `subprocess.run` to shell out to `git log` inside
  `SessionStartHook` so the session-start context includes recent commits.
- **Risk**: Low.
  - Arguments are hardcoded (`--oneline`, `--since=48 hours ago`, `--no-merges`).
  - No user input is interpolated into the argv.
  - `shell=False` (list form, no shell metacharacter expansion).
  - 5-second timeout bounds the call.
  - `shutil.which("git")` is used to resolve the binary; if git isn't installed
    the block is skipped.
- **Mitigation**: `# noqa: S404` / `# noqa: S603` comments document the
  acceptance. Users concerned about subprocess surface can disable the hook.
- **Status**: ✅ **ACCEPTABLE** — intentional.

Code excerpt:

```python
import shutil
import subprocess  # noqa: S404 — subprocess needed for git log

git_path = shutil.which("git")
if git_path:
    result = subprocess.run(  # noqa: S603
        [git_path, "log", "--oneline", "--since=48 hours ago", "--no-merges"],
        cwd=context.vault_path,
        capture_output=True,
        text=True,
        timeout=5,
    )
```

## Recent Hardening

### v0.4.1 — Path-traversal prefix-collision fix

`ompa/vault.py::_safe_resolve` previously used `str.startswith` to confirm a
resolved path stayed inside the vault, which could be bypassed by sibling
directories sharing a prefix (e.g., vault `…/vault` vs attacker target
`…/vault-evil`). The v0.4.1 release switched to `Path.relative_to`, which
raises `ValueError` on any escape. Regression test:
`tests/test_ompa.py::test_safe_resolve_prefix_collision`.

### v0.4.2 — MCP `vault_path` validation hardened

`ompa/mcp_server.py::handle_call_tool` previously did a substring check
(`".." in vault_path or vault_path in ("/", "C:\\", "C:/")`) that missed
absolute system paths (`/etc`, `C:\Windows`, `/root`). The v0.4.2 release adds
`_validate_vault_path` which:

- Rejects empty, non-string, or `..`-containing input.
- Resolves via `Path.expanduser().resolve()` before comparison so symlink
  tricks can't hide traversal.
- Rejects filesystem roots (any path whose resolved form equals its anchor).
- Rejects paths equal to or inside a hardcoded sensitive-root list
  (`/etc`, `/root`, `/boot`, `/sys`, `/proc`, `/dev`, `/usr`, `/bin`, `/sbin`,
  `/lib`, `/lib64`, `C:\Windows`, `C:\Program Files`, `C:\Program Files (x86)`,
  `C:\ProgramData`).
- Applies the same validation to the dual-vault `shared_vault_path` and
  `personal_vault_path` arguments.

Regression tests cover `..`, filesystem roots, platform-specific system
directories, empty strings, and dual-vault parameter rejection — see
`tests/test_ompa.py::TestMCPServer`.

### v0.4.2 — Broader credential redaction on export

`ompa/core.py::_sanitize_content` previously matched only OpenAI `sk-…`,
AWS `AKIA…`, and generic `(token|password|secret|api_key)\s*[:=]` patterns.
v0.4.2 introduces a centralized `_SECRET_PATTERNS` list that additionally
redacts:

- Anthropic API keys (`sk-ant-…`)
- OpenAI project keys (`sk-proj-…`)
- AWS temporary/identity keys (`ASIA…`, `AIDA…`, `AROA…`)
- GitHub PATs (classic `ghp_`, OAuth `gho_`, user-to-server `ghu_`,
  server-to-server `ghs_`, refresh `ghr_`, fine-grained `github_pat_…`)
- GitLab PATs (`glpat-…`)
- Slack tokens (`xoxb/p/a/r/s-…`)
- Google API keys (`AIza…`)
- Stripe live/test secret and restricted keys (`sk_live_`, `sk_test_`,
  `rk_live_`, `pk_live_`)
- JSON Web Tokens (`eyJ…eyJ….…`)
- PEM-encoded private keys (RSA, EC, DSA, OpenSSH, PGP, ENCRYPTED)
- Azure storage `AccountKey=…`
- MongoDB/Postgres/MySQL URIs with inline credentials

`tests/test_ompa.py::TestDualVault::test_sanitize_content_covers_broad_secret_set`
exercises the list; a companion `test_sanitize_content_preserves_safe_text`
guards against over-redaction of innocuous prose like "desk-work" or
"token review".

## Architecture Security Analysis

### ✅ Strengths

1. **No network exposure** — MCP server uses stdin/stdout; no listening sockets.
2. **Local-only storage** — filesystem + SQLite; no cloud calls at runtime.
3. **Input validation**
   - `_safe_resolve` for per-vault path traversal (relative_to-based).
   - `_validate_vault_path` for MCP vault/dual-vault arguments.
   - Frontmatter parsed with `python-frontmatter` (YAML safe loader).
   - Wikilink extraction is pure regex, no `eval`.
4. **Safe defaults**
   - Vault path defaults to `.`; no implicit writes outside it.
   - Dual-vault export requires explicit `shared_vault_path` + `personal_vault_path`.
5. **Dependency hygiene**
   - Core runtime deps: `typer`, `rich`, `python-frontmatter` (all permissive).
   - Optional `[semantic]` extras: `numpy`, `sentence-transformers`.
   - No mandatory network dependencies.
6. **Context-managed SQLite** — all `KnowledgeGraph._conn()` usage is wrapped
   in a context manager that commits on success, rolls back on exception, and
   always closes.

### Known Trade-offs

- **Semantic index format** is currently human-readable JSON; it is not a
  parsing threat (loaded via `json.load`), but v0.5.0 will switch to
  `numpy.savez_compressed` for performance. No security impact.
- **Classifier regexes** are simple substring/word-boundary patterns. They
  route messages, they do not gate security-sensitive actions.

## Dependency Security

| Dependency | Version | Risk | Notes |
|------------|---------|------|-------|
| typer | >=0.9.0 | Low | CLI framework, well-maintained |
| rich | >=13.0.0 | Low | Terminal output, no network |
| python-frontmatter | >=1.1.0 | Low | YAML parsing via safe loader |
| numpy (optional) | >=1.24.0 | Low | Array ops, no network |
| sentence-transformers (optional) | >=2.2.0 | Low\* | One-time model download from HuggingFace |

\* Users who want zero network traffic can pre-populate the HuggingFace cache
and run OMPA offline.

## Integration Security

### MCP Server
- Runs as a subprocess with stdin/stdout only.
- No listening socket; no network exposure.
- Host (Claude Desktop / Cursor / Windsurf) controls lifecycle.
- `vault_path` and dual-vault paths validated before every tool dispatch.
- **Risk**: Minimal.

### CLI Usage
- Direct filesystem access under the invoking user's permissions.
- No privilege escalation paths.
- **Risk**: Minimal.

### Python API
- Library import; inherits the calling process' sandbox/permissions.
- No network calls at import or during normal use.
- **Risk**: Minimal.

## Conclusion

OMPA is **safe for production use**. The only Bandit-flagged items are the
deliberate, sandboxed `git log` subprocess call used for session context, and
are documented / `noqa`-annotated. The v0.4.1 and v0.4.2 releases closed all
known traversal gaps in the MCP entrypoint and broadened the credential-
redaction patterns applied when exporting from the personal vault.

**✅ APPROVED for integration into Micap AI memory system.**

---

*Audit refreshed by Jarv (Tai agent) on 2026-04-14 for the v0.4.2 release.*
