# Release Checklist — OMPA

This document describes how OMPA is cut and published to PyPI.

## Pre-flight (already configured, kept here for reference)

### Trusted publishing (OIDC)

PyPI trusted publishing is configured for the `ompa` project against
`jmiaie/ompa` using the `publish` job in `.github/workflows/ci.yml` with the
`pypi` environment. No API token is stored in the repo — GitHub Actions
authenticates to PyPI via OIDC on tag push.

If this ever needs to be re-created:

1. https://pypi.org/manage/project/ompa/settings/publishing/ → Add publisher.
2. Repository: `jmiaie/ompa`.
3. Workflow: `ci.yml`.
4. Environment: `pypi`.

## Cutting a release

1. **Update the version.** Edit `pyproject.toml::project.version` and
   `ompa/__init__.py::__version__` (and `ompa/mcp_server.py::__version__`
   until P3.6 consolidates this into one source of truth).
2. **Run local gates.**

   ```bash
   pytest tests/ -v
   ruff check ompa/
   black --check ompa/
   ```

3. **Commit the version bump.**

   ```bash
   git add pyproject.toml ompa/__init__.py ompa/mcp_server.py
   git commit -m "Release v0.4.2"
   ```

4. **Tag and push.**

   ```bash
   git tag v0.4.2 -m "Release v0.4.2"
   git push origin main --tags
   ```

5. **Watch CI.** The `publish` job in `ci.yml` runs on `push: tags: ['v*']`,
   builds the wheel and sdist, and uploads to PyPI via OIDC. Check
   https://github.com/jmiaie/ompa/actions.

6. **Create a GitHub release.** Use the tag, paste the `CHANGELOG.md` stanza
   as the body (once P3.1 adds it).

## Manual fallback (only if CI publishing breaks)

```bash
pip install build twine
python -m build
twine check dist/*
twine upload dist/*   # prompts for credentials; prefer fixing OIDC instead
```

## Versioning

Semantic versioning. OMPA is still 0.x, so breaking changes are allowed on a
minor bump with a note in the changelog.

- Patch (`0.4.x`): bugs, docs, perf where the public API is unchanged.
- Minor (`0.5.0`): new features or intentional API/storage-format changes
  (e.g., the v0.5.0 semantic-index binary format swap).
- Major (`1.0.0`): stability commitment for the public Python API.

## What's published

| Artifact | Description |
|----------|-------------|
| `ompa-<ver>-py3-none-any.whl` | Installable wheel |
| `ompa-<ver>.tar.gz` | Source distribution |

Includes `ompa/` (all modules), `pyproject.toml`, `README.md`, `LICENSE`.
Excludes `tests/`, `docs/`, `CLAUDE.md`, development scripts.

## Post-release

1. Verify `pip install ompa==<ver>` from a clean venv.
2. Smoke test:

   ```bash
   ao init /tmp/ompa-smoke
   ao status --vault-path /tmp/ompa-smoke
   ao classify "We decided to use Postgres" --vault-path /tmp/ompa-smoke
   ```

3. Announce in release notes; bump `CHANGELOG.md`.
