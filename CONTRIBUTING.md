# Contributing to OMPA

Thanks for considering a contribution! This guide covers how to set up a
development environment, run the test suite, and submit a pull request.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/jmiaie/ompa.git
cd ompa

# Create a virtual environment (Python 3.10+)
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows

# Install with dev + semantic extras (editable)
pip install -e ".[dev,all]"
```

The `[dev]` extra installs pytest, black, and ruff. The `[all]` extra pulls in
numpy and sentence-transformers for semantic search tests.

## Running Tests

```bash
# Full suite (131 tests)
pytest tests/ -v

# With branch coverage
pytest tests/ --cov

# Single test class
pytest tests/test_ompa.py::TestVault -v

# Single test
pytest tests/test_ompa.py::TestVault::test_extract_wikilinks_shared_behavior -v
```

Tests use `tempfile.TemporaryDirectory` — no external services or fixtures needed.

## Linting & Formatting

The CI pipeline gates on ruff and black. Run locally before pushing:

```bash
ruff check ompa/
black --check ompa/
```

To auto-fix:

```bash
ruff check --fix ompa/
black ompa/
```

## Project Layout

```
ompa/
  __init__.py        # Package entry, version from importlib.metadata
  core.py            # Ompa orchestrator (hooks, write, sync, search)
  vault.py           # Vault structure, Note model, wikilink extraction
  palace.py          # Memory Palace (wings, rooms, drawers, halls, tunnels)
  knowledge_graph.py # Temporal KG (SQLite triples with validity windows)
  semantic.py        # Hybrid semantic + keyword search (sentence-transformers)
  classifier.py      # Message classifier (15 types, regex-based)
  config.py          # Dual-vault config and content routing
  hooks.py           # Lifecycle hooks (session_start, post_tool, stop)
  mcp_server.py      # MCP JSON-RPC server (19 tools)
  cli.py             # Typer CLI (21 commands)
tests/
  test_ompa.py       # All tests (will be split per module in a future PR)
```

## Adding a New MCP Tool

1. Define the handler function in `mcp_server.py` (e.g., `ao_my_tool`).
2. Add a `TOOLS` entry with the input schema.
3. Add a `_DISPATCH` entry mapping the tool name to a lambda that calls
   your handler.
4. Add a test in `tests/test_ompa.py::TestMCPServer`.

## Adding a New Message Type

1. Add the enum value to `classifier.py::MessageType`.
2. Add patterns to `classifier.py::MessageClassifier.PATTERNS`.
3. Add a test in `tests/test_ompa.py::TestClassifier`.
4. Update the `suggested_folder` mapping in `classify()` if needed.

## Pull Request Guidelines

- One logical change per PR. Don't bundle unrelated fixes.
- All tests must pass (`pytest tests/ -v`).
- All lint must pass (`ruff check ompa/ && black --check ompa/`).
- Write a descriptive title (under 70 characters) and a short summary.
- If you add a feature, add a test for it.
- If you fix a bug, add a regression test.

## Code Style

- Python 3.10+ — use PEP 604 union syntax (`X | None`, not `Optional[X]`).
- Format with black (default settings).
- Lint with ruff (config in pyproject.toml).
- Docstrings: one-line summary + Args/Returns for non-trivial public methods.
- Import order: stdlib, third-party, local (ruff enforces this).

## License

By contributing you agree that your contributions will be licensed under the
MIT License.
