# Release Checklist — AgnosticObsidian

## Pre-Release (do once)

### 1. Create PyPI Account + API Token
1. Go to https://pypi.org/account/register
2. Verify email
3. Go to https://pypi.org/manage/account/api-tokens/
4. Create token scoped to `agnostic-obsidian` project
5. Save token — you'll use it as `PYPI_API_TOKEN` in GitHub Secrets

### 2. Add GitHub Secrets
```
Repository → Settings → Secrets and variables → Actions
Add:
- PYPI_API_TOKEN = pypi-...
```

### 3. Configure Trusted Publishing (recommended)
On PyPI, go to your project → Publishing → Add a new publisher:
- GitHub repository: `YOUR_USERNAME/agnostic-obsidian`
- Workflow filename: `ci.yml`
- Environment: `pypi`

This eliminates API tokens entirely (OIDC-based).

---

## Release Steps

### Option A: Via GitHub Actions (recommended)

```bash
# 1. Update version in __init__.py and pyproject.toml
# 2. Tag and push
git tag v0.1.0 -m "Release v0.1.0"
git push origin v0.1.0
# GitHub Actions automatically:
#   - Runs tests on 3 Python versions
#   - Runs ruff + black linting
#   - Builds wheel
#   - Publishes to PyPI on tag push
```

### Option B: Manual Publish

```bash
# Install build tools
pip install build twine --break-system-packages

# Build
cd agnostic-obsidian
python -m build

# Check
twine check dist/*

# Upload (you'll need PYPI_API_TOKEN)
TWINE_PASSWORD=YOUR_TOKEN twine upload dist/*
```

---

## Version Numbering

We use Semantic Versioning: `MAJOR.MINOR.PATCH`
- `v0.1.0` — initial beta release
- `v0.2.0` — add features (e.g., MCP server tools)
- `v0.2.1` — bug fixes

---

## What Gets Published

| Artifact | Description |
|----------|-------------|
| Wheel (`agnostic_obsidian-*.whl`) | Installable Python package |
| Source tarball (`agnostic_obsidian-*.tar.gz`) | Source distribution |

### Includes:
- `__init__.py`, `core.py`, `vault.py`, `palace.py`, `knowledge_graph.py`, `hooks.py`, `classifier.py`, `semantic.py`, `mcp_server.py`, `cli.py`
- `pyproject.toml`
- `README.md` (long_description from this)
- `LICENSE` (MIT)

### Excludes (via `.gitignore`):
- `.github/`
- `tests/`
- `demo.cast`
- `CLAUDE.md`, `PUSH.md`, `*.md` in repo root (not included in package)

---

## Post-Release

1. Create GitHub Release with release notes
2. Post to:
   - Twitter/X: "Just released agnostic-obsidian v0.1.0 — universal AI agent memory layer. pip install agnostic-obsidian"
   - Hacker News: "Show: AgnosticObsidian — universal AI memory layer"
   - r/LocalLLaMA, r/ClaudeAI
   - Discord community

---

## Quick Publish Commands (one-time setup)

```bash
# Clone fresh
git clone https://github.com/YOUR_USERNAME/agnostic-obsidian.git
cd agnostic-obsidian

# Edit version
# __init__.py: __version__ = "0.1.0"
# pyproject.toml: version = "0.1.0"

# Tag
git tag v0.1.0
git push origin v0.1.0

# Done — GitHub Actions handles the rest
```

---

## Dependencies (must be in pyproject.toml)

Core runtime:
- numpy>=1.24.0
- sentence-transformers>=2.2.0
- typer>=0.9.0
- rich>=13.0.0
- python-frontmatter>=1.1.0
- watchdog>=3.0.0

Dev (not published):
- pytest>=7.0.0
- ruff>=0.1.0
- black>=23.0.0

---

## Verification After Install

```bash
pip install agnostic-obsidian

# Test
ao --version    # or: python -m agnostic_obsidian.cli --version
ao init /tmp/test
ao status --vault-path /tmp/test
```
