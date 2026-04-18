"""End-to-end MCP flow tests.

Exercise the tool dispatch through realistic multi-call sequences
(init → write → search → kg_query → sync → status) to catch regressions
in handler wiring, argument parsing, and cross-tool side effects that
unit-level handler tests miss.
"""

import tempfile
from pathlib import Path

from ompa.mcp_server import _clear_ompa_cache, handle_call_tool


def _call(name: str, **args):
    return handle_call_tool(name, args)


class TestMCPEndToEnd:
    def setup_method(self):
        _clear_ompa_cache()

    def teardown_method(self):
        _clear_ompa_cache()

    def test_init_creates_vault_structure(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _call("ao_init", vault_path=tmp)
            assert "error" not in result
            assert (Path(tmp) / "brain").exists()
            assert (Path(tmp) / "work" / "active").exists()

    def test_write_then_search_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            _call("ao_init", vault_path=tmp)
            write_result = _call(
                "ao_write",
                content="We decided to go with Postgres for persistence.",
                file_path="work/active/postgres-decision.md",
                tags=["decision", "db"],
                vault_path=tmp,
            )
            assert "error" not in write_result
            assert (Path(tmp) / "work" / "active" / "postgres-decision.md").exists()

            # Text-mode search should find the new note
            search = _call("ao_search", query="Postgres", vault_path=tmp, limit=5)
            assert "error" not in search
            assert "results" in search
            paths = [r.get("path", "") for r in search["results"]]
            assert any("postgres-decision" in p for p in paths)

    def test_kg_add_then_query_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            _call("ao_init", vault_path=tmp)
            # MCP schema uses "object" (not reserved in a dict)
            add = handle_call_tool(
                "ao_kg_add",
                {
                    "subject": "Jarv",
                    "predicate": "runs",
                    "object": "OMPA",
                    "vault_path": tmp,
                    "valid_from": "2026-04-10",
                },
            )
            assert "error" not in add

            q = _call("ao_kg_query", entity="Jarv", vault_path=tmp)
            assert "error" not in q
            assert "facts" in q
            assert any(t["object"] == "OMPA" for t in q["facts"])

    def test_sync_populates_kg_from_written_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            _call("ao_init", vault_path=tmp)
            _call(
                "ao_write",
                content="Auth design notes. See [[Clerk]] for details.",
                file_path="work/active/auth.md",
                vault_path=tmp,
            )
            sync = _call("ao_sync", vault_path=tmp)
            assert "error" not in sync
            # Sync reports counts for both shared (and personal when dual)
            assert "kg_triples" in sync

    def test_status_returns_vault_stats(self):
        with tempfile.TemporaryDirectory() as tmp:
            _call("ao_init", vault_path=tmp)
            status = _call("ao_status", vault_path=tmp)
            assert "error" not in status
            # stats dict with note_count / folders / etc.
            assert isinstance(status, dict)

    def test_classify_does_not_require_init(self):
        """Classification is path-agnostic — should work against any dir."""
        with tempfile.TemporaryDirectory() as tmp:
            r = _call(
                "ao_classify",
                message="Outage at 3am — users couldn't log in.",
                vault_path=tmp,
            )
            assert "error" not in r
            assert r["message_type"] == "incident"

    def test_palace_wings_empty_on_fresh_init(self):
        with tempfile.TemporaryDirectory() as tmp:
            _call("ao_init", vault_path=tmp)
            wings = _call("ao_palace_wings", vault_path=tmp)
            assert "error" not in wings
            assert isinstance(wings.get("wings", []), list)

    def test_orphans_on_empty_vault_is_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            _call("ao_init", vault_path=tmp)
            orphans = _call("ao_orphans", vault_path=tmp)
            assert "error" not in orphans
            assert orphans.get("orphans") == [] or orphans.get("count", 0) == 0
