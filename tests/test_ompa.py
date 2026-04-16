"""
OMPA test suite.
Run: pytest tests/ -v
"""

import os
import tempfile
from pathlib import Path

import pytest


class TestPalace:
    """Test palace metadata layer."""

    def test_create_wing(self):
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project", keywords=["analytics"])
            wings = p.list_wings()
            assert len(wings) == 1
            assert wings[0]["name"] == "Orion"

    def test_rooms_and_drawers(self):
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project")
            p.create_room("Orion", "auth-migration")
            p.link_drawer("Orion", "auth-migration", "work/active/auth.md")
            drawers = p.get_drawers("Orion", "auth-migration")
            assert "work/active/auth.md" in drawers

    def test_halls(self):
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project")
            p.create_room("Orion", "auth-migration")
            p.add_hall(
                "Orion", "auth-migration", "hall_facts", content="Team chose Clerk"
            )
            hall = p.get_hall("Orion", "auth-migration", "hall_facts")
            assert "Clerk" in hall

    def test_tunnels(self):
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project")
            p.create_wing("Kai", type="person")
            p.create_room("Orion", "auth-migration")
            p.create_tunnel("Kai", "Orion", "auth-migration")
            tunnels = p.find_tunnels("Orion", "Kai")
            assert len(tunnels) == 1

    def test_stats(self):
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project")
            p.create_room("Orion", "room1")
            p.link_drawer("Orion", "room1", "file.md")
            stats = p.stats()
            assert stats["wing_count"] == 1
            assert stats["room_count"] == 1

    def test_create_wing_idempotent_preserves_rooms(self):
        """Regression: create_wing on existing wing must NOT wipe rooms.

        Prior bug (pre-0.4.2): create_wing unconditionally overwrote
        ``rooms: {}``, destroying all rooms/drawers/halls under the wing.
        """
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project", keywords=["analytics"])
            p.create_room("Orion", "auth")
            p.link_drawer("Orion", "auth", "work/auth.md")
            p.add_hall("Orion", "auth", "hall_facts", "we chose Clerk")

            # Re-create same wing — must preserve everything.
            p.create_wing("Orion", type="project", keywords=["analytics"])

            assert "auth" in p.list_rooms("Orion")
            assert "work/auth.md" in p.get_drawers("Orion", "auth")
            assert "Clerk" in p.get_hall("Orion", "auth", "hall_facts")

    def test_create_room_idempotent_preserves_drawers_and_halls(self):
        """Regression: create_room on existing room must NOT wipe drawers/halls.

        Prior bug (pre-0.4.2): create_room unconditionally overwrote
        ``drawers: []`` and ``halls: {}``.
        """
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion")
            p.create_room("Orion", "auth")
            p.link_drawer("Orion", "auth", "work/auth.md")
            p.add_hall("Orion", "auth", "hall_facts", "we chose Clerk")

            # Re-create same room — must preserve drawers and halls.
            p.create_room("Orion", "auth")

            assert "work/auth.md" in p.get_drawers("Orion", "auth")
            assert "Clerk" in p.get_hall("Orion", "auth", "hall_facts")

    def test_create_wing_preserves_existing_type_and_keywords(self):
        """Re-creating an existing wing must not silently override metadata."""
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            p.create_wing("Orion", type="project", keywords=["analytics"])

            # Re-call with different type/keywords — must NOT clobber the
            # original metadata. Callers wanting to update should use a
            # dedicated update helper (none exists yet).
            p.create_wing("Orion", type="person", keywords=["different"])

            wing = p.get_wing("Orion")
            assert wing["type"] == "project"
            assert wing["keywords"] == ["analytics"]

    def test_batch_defers_disk_writes(self):
        """batch() must collapse N mutations into one JSON write."""
        from unittest.mock import patch
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            # Patch the actual disk-write helper to count calls.
            with patch.object(
                p, "_save_now", wraps=p._save_now
            ) as save_now:
                with p.batch():
                    for i in range(5):
                        p.create_wing(f"wing-{i}")
                        p.create_room(f"wing-{i}", "r")
                        p.link_drawer(f"wing-{i}", "r", f"f{i}.md")
                assert save_now.call_count == 1, (
                    f"Expected 1 disk write during batch, got {save_now.call_count}"
                )

            # Verify data actually persisted.
            p2 = Palace(os.path.join(tmpdir, ".palace"))
            assert len(p2.list_wings()) == 5

    def test_batch_flushes_on_exception(self):
        """If the batch block raises, queued changes must still flush so
        partial progress isn't silently lost."""
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            try:
                with p.batch():
                    p.create_wing("Orion")
                    raise RuntimeError("boom")
            except RuntimeError:
                pass

            # Fresh Palace must see Orion on disk.
            p2 = Palace(os.path.join(tmpdir, ".palace"))
            assert "Orion" in [w["name"] for w in p2.list_wings()]

    def test_batch_is_reentrant(self):
        """Nested batches only flush at the outermost exit."""
        from unittest.mock import patch
        from ompa import Palace

        with tempfile.TemporaryDirectory() as tmpdir:
            p = Palace(os.path.join(tmpdir, ".palace"))
            with patch.object(p, "_save_now", wraps=p._save_now) as save_now:
                with p.batch():
                    p.create_wing("A")
                    with p.batch():
                        p.create_wing("B")
                    # Inner exit must NOT have flushed yet.
                    assert save_now.call_count == 0
                assert save_now.call_count == 1


class TestKnowledgeGraph:
    """Test temporal knowledge graph."""

    def test_add_triple(self):
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
            triples = kg.query_entity("Kai")
            assert len(triples) == 1
            assert triples[0].predicate == "works_on"

    def test_query_entity_filtered(self):
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            kg.add_triple(
                "Kai",
                "works_on",
                "Orion",
                valid_from="2025-06-01",
                valid_to="2025-12-01",
            )
            kg.add_triple("Kai", "works_on", "OMPA", valid_from="2026-01-01")
            triples = kg.query_entity("Kai", as_of="2025-09-01")
            assert any(t.object == "Orion" for t in triples)
            triples = kg.query_entity("Kai", as_of="2026-03-01")
            assert all(t.object == "OMPA" for t in triples)

    def test_invalidate(self):
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
            kg.invalidate("Kai", "works_on", "Orion", ended="2025-12-31")
            # Should not appear in current query
            triples = kg.query_entity("Kai", as_of="2026-06-01")
            assert len(triples) == 0

    def test_query_relation(self):
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
            kg.add_triple("Kai", "likes", "coffee")
            triples = kg.query_relation("Kai", "works_on")
            assert len(triples) == 1
            assert triples[0].object == "Orion"

    def test_query_relation_excludes_invalidated(self):
        """Regression: query_relation must apply temporal filter, mirroring
        query_entity. Pre-0.4.2 it returned invalidated triples too."""
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
            kg.invalidate("Kai", "works_on", "Orion", ended="2025-12-31")

            # Default (today) — invalidated, must be excluded.
            triples = kg.query_relation("Kai", "works_on")
            assert triples == []

            # Historical query — was valid then, must be included.
            triples = kg.query_relation("Kai", "works_on", as_of="2025-09-01")
            assert len(triples) == 1
            assert triples[0].object == "Orion"

    def test_query_relation_respects_valid_from(self):
        """A triple with future valid_from must not appear before it starts."""
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            kg.add_triple("Kai", "works_on", "Helios", valid_from="2027-01-01")

            triples = kg.query_relation("Kai", "works_on", as_of="2026-06-01")
            assert triples == []
            triples = kg.query_relation("Kai", "works_on", as_of="2027-06-01")
            assert len(triples) == 1
            assert triples[0].object == "Helios"

    def test_timeline(self):
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
            kg.add_triple("Kai", "completed", "auth-migration", valid_from="2026-02-01")
            timeline = kg.timeline("Kai")
            assert len(timeline) == 2
            dates = [e["date"] for e in timeline if e["date"]]
            assert dates == sorted(dates)

    def test_stats(self):
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
            kg.add_triple("Jarv", "works_on", "OMPA", valid_from="2026-04-10")
            stats = kg.stats()
            assert stats["entity_count"] == 4

    def test_atomic_add_triple(self):
        """add_triple should use a single connection (atomic)."""
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            kg.add_triple("A", "rel", "B", valid_from="2026-01-01")
            # Both entities and the triple should exist
            stats = kg.stats()
            assert stats["entity_count"] == 2
            assert stats["triple_count"] == 1


class TestClassifier:
    """Test message classifier."""

    def test_decision(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("We decided to go with Postgres")
        assert result.message_type.value == "decision"
        assert result.confidence >= 0.3

    def test_win(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("We won the enterprise deal!")
        assert result.message_type.value == "win"

    def test_incident(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("The auth bug is blocking deployment")
        assert result.message_type.value == "incident"

    def test_question(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("Should we use Clerk or Auth0 for auth?")
        assert result.message_type.value == "question"

    def test_suggestion(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("We should add tests before merging")
        assert result.message_type.value in ("task", "unknown")

    def test_blocker(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("I'm blocked on the API design")
        assert result.message_type.value == "architecture"

    def test_learning(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("TIL that Postgres has built-in full-text search")
        assert result.message_type.value in ("brain-dump", "code", "unknown")

    def test_retrospective(self):
        from ompa import MessageClassifier

        c = MessageClassifier()
        result = c.classify("In our retrospective we found three issues")
        assert result.message_type.value == "meeting"


class TestVault:
    """Test vault management."""

    def test_init_creates_structure(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            assert (vault.vault_path / "brain").is_dir()
            assert (vault.vault_path / "work" / "active").is_dir()
            assert (vault.vault_path / "org" / "people").is_dir()

    def test_get_stats_empty_vault(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            stats = vault.get_stats()
            assert stats["total_notes"] == 0
            assert stats["orphans"] == 0

    def test_update_and_get_brain_note(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("Test Note", "Hello world")
            note = vault.get_brain_note("Test Note")
            assert note is not None
            assert "Hello world" in note.content

    def test_brain_note_path_traversal_blocked(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            with pytest.raises(ValueError, match="Invalid brain note name"):
                vault.get_brain_note("../../etc/passwd")

    def test_update_brain_note_path_traversal_blocked(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            with pytest.raises(ValueError, match="Invalid brain note name"):
                vault.update_brain_note("../../etc/evil", "pwned")

    def test_validate_write_blocks_outside_vault(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            result = vault.validate_write("../../etc/passwd")
            assert result["valid"] is False
            assert "outside the vault" in result["warnings"][0]

    def test_create_from_template_path_traversal_blocked(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            with pytest.raises((ValueError, FileNotFoundError)):
                vault.create_from_template("../../etc/evil", "../../tmp/pwned.md")

    def test_note_save_utf8(self):
        from ompa.vault import Note
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unicode.md"
            note = Note(path=path, content="Héllo wörld 日本語")
            note.save()
            loaded = Note.from_file(path)
            assert "日本語" in loaded.content

    def test_safe_resolve_prefix_collision(self):
        """_safe_resolve should block prefix-collision bypasses like vault vs vault-evil."""
        from ompa.vault import _safe_resolve
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "vault"
            base.mkdir()
            evil = Path(tmpdir) / "vault-evil"
            evil.mkdir()
            # This should NOT be allowed — "vault-evil" starts with "vault" as a string
            # but is NOT a child of "vault"
            with pytest.raises(ValueError, match="Path traversal blocked"):
                _safe_resolve(base, "../vault-evil/secret.md")

    def test_search_by_name(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("Auth Design", "Authentication design doc")
            results = vault.search_by_name("auth")
            assert len(results) >= 1

    def test_list_notes_cache_reuses_parsed_note(self):
        """Unchanged files should be served from the cache as the same Note
        object across calls — this is the core P1.1 optimization."""
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("cache-test", "hello world")
            first = {n.path: n for n in vault.list_notes()}
            second = {n.path: n for n in vault.list_notes()}
            # Every path present in both scans must return the identical
            # object — proving the cache returned the cached Note instead
            # of re-parsing from disk.
            shared = set(first) & set(second)
            assert shared, "Expected at least one note to appear in both scans"
            for path in shared:
                assert first[path] is second[path]

    def test_list_notes_cache_invalidates_on_mtime_change(self):
        """When a file's mtime changes, the cache must re-parse it."""
        import os
        import time
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("mtime-test", "original content")
            target = vault.config.brain_folder / "mtime-test.md"
            [initial] = [n for n in vault.list_notes() if n.path == target]
            assert "original content" in initial.content

            # Rewrite with a distinctly later mtime. Use os.utime to avoid
            # depending on real time passing during the test.
            target.write_text(
                "---\ntitle: mtime-test\n---\nupdated content",
                encoding="utf-8",
            )
            new_time = time.time() + 10
            os.utime(target, (new_time, new_time))

            [refreshed] = [n for n in vault.list_notes() if n.path == target]
            assert "updated content" in refreshed.content
            assert refreshed is not initial

    def test_list_notes_cache_explicit_invalidation(self):
        """invalidate_path / invalidate_cache drop cached entries."""
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("invalidate-test", "content")
            target = vault.config.brain_folder / "invalidate-test.md"

            _ = vault.list_notes()
            assert target in vault._note_cache

            vault.invalidate_path(target)
            assert target not in vault._note_cache

            _ = vault.list_notes()
            assert target in vault._note_cache

            vault.invalidate_cache()
            assert vault._note_cache == {}

    def test_extract_wikilinks_shared_behavior(self):
        """P2.4: module-level extract_wikilinks normalizes consistently.

        Both Note._extract_wikilinks and KG populate use this function,
        so this test documents the canonical contract:
          - [[target|display]] → keep target, drop display
          - [[SOUL.md]]        → strip trailing .md
          - whitespace trimmed, empty targets dropped
        """
        from ompa.vault import extract_wikilinks

        result = extract_wikilinks(
            "See [[SOUL.md]] and [[Design|the design doc]] and [[ ]] and [[Auth]]"
        )
        assert result == ["SOUL", "Design", "Auth"]

    def test_extract_wikilinks_empty_and_edge_cases(self):
        """Edge cases for the shared wikilink extractor."""
        from ompa.vault import extract_wikilinks

        assert extract_wikilinks("") == []
        assert extract_wikilinks("no links here") == []
        assert extract_wikilinks("[[]]") == []
        assert extract_wikilinks("[[ .md ]]") == []
        # Nested-looking patterns: regex is non-greedy so inner wins
        assert extract_wikilinks("[[a]] [[b]]") == ["a", "b"]


class TestHooks:
    """Test lifecycle hooks."""

    def test_session_start_hook(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            result = ao.session_start()
            assert result.success
            assert "Session Context" in result.output

    def test_user_message_hook(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            result = ao.handle_message("We decided to use Postgres")
            assert result.success
            assert "Classification" in result.output

    def test_post_tool_hook_skip_non_write(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            result = ao.post_tool("read", {"file_path": "test.md"})
            assert result.success
            assert "skipped" in result.output

    def test_stop_hook(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            result = ao.stop()
            assert result.success
            assert "Wrap-Up" in result.output

    def test_estimate_tokens_helper(self):
        """P1.8: tokens_hint should use ~chars/4 approximation, not word count.

        Prior bug: ``tokens_hint = len(output.split())`` was ~30–40% too low
        because it ignored subword tokenization. The fixed estimator uses
        the standard GPT/Claude heuristic of ~4 characters per token.
        """
        from ompa.hooks import _estimate_tokens

        # Empty text → 0.
        assert _estimate_tokens("") == 0
        # Single-character → at least 1 (we never round to 0 for non-empty).
        assert _estimate_tokens("a") == 1
        # 80-char string → 20 tokens (80 // 4).
        eighty = "word " * 16  # 80 chars
        assert len(eighty) == 80
        assert _estimate_tokens(eighty) == 20
        # Word count would report 16 here — proving we diverged from the
        # old behavior (and correctly, since GPT tokenizes "word" + " ").
        assert _estimate_tokens(eighty) != len(eighty.split())


class TestMCPServer:
    """Test MCP server tool dispatch."""

    def test_handle_list_tools(self):
        from ompa.mcp_server import handle_list_tools

        result = handle_list_tools()
        assert "tools" in result
        tool_names = [t["name"] for t in result["tools"]]
        assert "ao_session_start" in tool_names
        assert "ao_classify" in tool_names
        assert "ao_kg_query" in tool_names

    def test_handle_call_tool_classify(self):
        from ompa.mcp_server import handle_call_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_call_tool(
                "ao_classify",
                {
                    "message": "We decided to use Postgres",
                    "vault_path": tmpdir,
                },
            )
            assert "message_type" in result
            assert result["message_type"] == "decision"

    def test_handle_call_tool_unknown(self):
        from ompa.mcp_server import handle_call_tool

        result = handle_call_tool("nonexistent_tool", {})
        assert "error" in result

    def test_handle_call_tool_missing_arg(self):
        from ompa.mcp_server import handle_call_tool

        result = handle_call_tool("ao_classify", {"vault_path": "."})
        assert "error" in result
        assert "Missing" in result["error"]

    def test_vault_path_traversal_blocked(self):
        from ompa.mcp_server import handle_call_tool

        result = handle_call_tool("ao_status", {"vault_path": "/"})
        assert "error" in result
        assert "Invalid" in result["error"]

    def test_vault_path_dotdot_rejected(self):
        from ompa.mcp_server import handle_call_tool

        result = handle_call_tool(
            "ao_status", {"vault_path": "../../../etc"}
        )
        assert "error" in result
        assert "Invalid" in result["error"]

    def test_vault_path_system_roots_rejected(self):
        """Absolute paths into system directories must be rejected."""
        import sys
        from ompa.mcp_server import handle_call_tool

        if sys.platform == "win32":
            bad_paths = [
                "C:\\Windows",
                "C:\\Windows\\System32",
                "C:\\Program Files",
                "C:\\",
                "C:/",
            ]
        else:
            bad_paths = ["/etc", "/etc/ssh", "/root", "/usr/bin", "/"]
        for bad in bad_paths:
            result = handle_call_tool("ao_status", {"vault_path": bad})
            assert "error" in result, f"Expected error for {bad!r}"
            assert "Invalid" in result["error"], (
                f"Expected 'Invalid' in error for {bad!r}: {result['error']}"
            )

    def test_vault_path_empty_rejected(self):
        from ompa.mcp_server import handle_call_tool

        result = handle_call_tool("ao_status", {"vault_path": ""})
        assert "error" in result
        assert "Invalid" in result["error"]

    def test_shared_and_personal_vault_paths_validated(self):
        """Dual-vault paths must pass through the same validator."""
        import sys
        from ompa.mcp_server import handle_call_tool

        bad = "C:\\Windows" if sys.platform == "win32" else "/etc"
        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_call_tool(
                "ao_write",
                {
                    "content": "hello",
                    "vault_path": tmpdir,
                    "shared_vault_path": bad,
                    "personal_vault_path": tmpdir,
                },
            )
            assert "error" in result
            assert "shared_vault_path" in result["error"]

    def test_limit_capped(self):
        from ompa.mcp_server import handle_call_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_call_tool(
                "ao_search",
                {
                    "query": "test",
                    "vault_path": tmpdir,
                    "limit": 999999,
                },
            )
            # Should not crash; limit is silently capped to 100
            assert "results" in result or "error" not in result

    def test_ompa_cache_reuses_instance_across_calls(self):
        """Repeated tool calls against the same vault must return the same
        cached Ompa instance so we don't reload the sentence-transformers
        model and re-parse the semantic index on every MCP tool invocation."""
        from ompa.mcp_server import _get_ompa, _clear_ompa_cache

        with tempfile.TemporaryDirectory() as tmpdir:
            _clear_ompa_cache()
            first = _get_ompa(vault_path=tmpdir, enable_semantic=False)
            second = _get_ompa(vault_path=tmpdir, enable_semantic=False)
            assert first is second

            # Different enable_semantic flag → different cache entry.
            third = _get_ompa(vault_path=tmpdir, enable_semantic=True)
            assert third is not first

            # Different vault path → different cache entry.
            with tempfile.TemporaryDirectory() as other:
                fourth = _get_ompa(vault_path=other, enable_semantic=False)
                assert fourth is not first

            _clear_ompa_cache()
            fifth = _get_ompa(vault_path=tmpdir, enable_semantic=False)
            assert fifth is not first  # cache was cleared

    def test_ompa_cache_dual_vault_keyed_by_both_paths(self):
        from ompa.mcp_server import _get_ompa, _clear_ompa_cache

        with tempfile.TemporaryDirectory() as shared, tempfile.TemporaryDirectory() as personal:
            _clear_ompa_cache()
            a = _get_ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            b = _get_ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            assert a is b
            _clear_ompa_cache()


class TestOmpa:
    """Test core Ompa integration."""

    def test_session_start(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            result = ao.session_start()
            assert result.success == True

    def test_classify(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            c = ao.classify("We decided to go with Postgres")
            assert c.message_type.value == "decision"

    def test_kg_integration(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            ao.kg.add_triple("Jarv", "works_on", "OMPA", valid_from="2026-04-10")
            triples = ao.kg.query_entity("Jarv")
            assert len(triples) == 1
            assert triples[0].object == "OMPA"

    def test_stop(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            result = ao.stop()
            assert result.success == True

    def test_backward_compat_alias(self):
        """Verify AgnosticObsidian still works as an alias."""
        from ompa import AgnosticObsidian, Ompa

        assert AgnosticObsidian is Ompa

    def test_python_m_ompa_entrypoint(self):
        """Verify __main__.py exists for python -m ompa."""
        import importlib

        spec = importlib.util.find_spec("ompa.__main__")
        assert spec is not None


class TestKGPopulation:
    """Test KG auto-population from vault notes."""

    def test_populate_from_note_wikilinks(self):
        """Wikilinks should create links_to triples."""
        from ompa import KnowledgeGraph
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            # Create a note with wikilinks
            vault_path = Path(tmpdir)
            note_path = vault_path / "brain" / "Test.md"
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(
                "---\ndate: 2026-04-10\ntags: [auth, security]\n---\n"
                "This links to [[North Star]] and [[Key Decisions]].",
                encoding="utf-8",
            )
            count = kg.populate_from_note(note_path, vault_path)
            assert count >= 2  # at least 2 wikilinks
            triples = kg.query_entity("Test")
            link_triples = [t for t in triples if t.predicate == "links_to"]
            assert len(link_triples) == 2

    def test_populate_from_note_tags(self):
        """Frontmatter tags should create has_tag triples."""
        from ompa import KnowledgeGraph
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            vault_path = Path(tmpdir)
            note_path = vault_path / "brain" / "Tagged.md"
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(
                "---\ndate: 2026-04-10\ntags: [auth, security]\n---\nContent here.",
                encoding="utf-8",
            )
            count = kg.populate_from_note(note_path, vault_path)
            triples = kg.query_entity("Tagged")
            tag_triples = [t for t in triples if t.predicate == "has_tag"]
            assert len(tag_triples) == 2
            tags = {t.object for t in tag_triples}
            assert "auth" in tags
            assert "security" in tags

    def test_populate_from_note_folders(self):
        """Notes should get in_folder triples from their directory."""
        from ompa import KnowledgeGraph
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            vault_path = Path(tmpdir)
            note_path = vault_path / "work" / "active" / "Auth.md"
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(
                "---\ndate: 2026-04-10\n---\nAuth work note.",
                encoding="utf-8",
            )
            count = kg.populate_from_note(note_path, vault_path)
            triples = kg.query_entity("Auth")
            folder_triples = [t for t in triples if t.predicate == "in_folder"]
            assert len(folder_triples) == 1
            assert folder_triples[0].object == "work"

    def test_populate_from_vault(self):
        """populate_from_vault should scan all notes."""
        from ompa import KnowledgeGraph
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            vault_path = Path(tmpdir)
            # Create multiple notes
            for name in ["Note1", "Note2"]:
                p = vault_path / "brain" / f"{name}.md"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(
                    f"---\ndate: 2026-04-10\n---\nSee [[{name}Link]].",
                    encoding="utf-8",
                )
            total = kg.populate_from_vault(vault_path)
            assert total >= 4  # at least 2 links + 2 folders
            stats = kg.stats()
            assert stats["entity_count"] >= 4

    def test_session_start_auto_populates_kg(self):
        """session_start should auto-populate KG if empty."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
            # Create a note with content before starting session
            brain = vault_path / "brain"
            brain.mkdir(parents=True, exist_ok=True)
            (brain / "Test.md").write_text(
                "---\ndate: 2026-04-10\ntags: [test]\n---\nSee [[North Star]].",
                encoding="utf-8",
            )
            ao = Ompa(tmpdir, enable_semantic=False)
            ao.session_start()
            stats = ao.kg.stats()
            assert stats["triple_count"] > 0

    def test_sync(self):
        """sync() should rebuild KG and palace."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
            brain = vault_path / "brain"
            brain.mkdir(parents=True, exist_ok=True)
            (brain / "Sync Test.md").write_text(
                "---\ndate: 2026-04-10\n---\nContent [[Link]].",
                encoding="utf-8",
            )
            ao = Ompa(tmpdir, enable_semantic=False)
            result = ao.sync()
            assert result["kg_triples"] > 0
            assert result["palace_wings"] >= 0

    def test_update_brain_syncs_kg(self):
        """update_brain should auto-update KG."""
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            ao.update_brain("Decisions", "We chose [[Postgres]] for the DB.")
            triples = ao.kg.query_entity("Decisions")
            link_triples = [t for t in triples if t.predicate == "links_to"]
            assert len(link_triples) == 1
            assert link_triples[0].object == "Postgres"

    def test_mcp_kg_populate(self):
        """MCP ao_kg_populate tool should work."""
        from ompa.mcp_server import handle_call_tool
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
            brain = vault_path / "brain"
            brain.mkdir(parents=True, exist_ok=True)
            (brain / "MCP Test.md").write_text(
                "---\ndate: 2026-04-10\n---\nSee [[Link]].",
                encoding="utf-8",
            )
            result = handle_call_tool("ao_kg_populate", {"vault_path": tmpdir})
            assert result["success"] is True
            assert result["triples_added"] > 0

    def test_mcp_sync(self):
        """MCP ao_sync tool should work."""
        from ompa.mcp_server import handle_call_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_call_tool("ao_sync", {"vault_path": tmpdir})
            assert result["success"] is True
            assert "kg_triples" in result

    def test_repopulate_cleans_up_stale_wikilink_triples(self):
        """P1.7: When a note is re-populated after removing a wikilink,
        the old links_to triple must be deleted so the KG stays consistent
        with the note on disk."""
        from pathlib import Path as _Path
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = _Path(tmpdir)
            note = vault_path / "work" / "active" / "Auth.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(
                "---\ndate: 2026-04-10\n---\nLinks to [[Design]] and [[Backup]].",
                encoding="utf-8",
            )

            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            kg.populate_from_note(note, vault_path)

            initial = kg.query_relation("Auth", "links_to")
            initial_targets = {t.object for t in initial}
            assert "Design" in initial_targets
            assert "Backup" in initial_targets

            # Edit the note — drop the Backup link.
            note.write_text(
                "---\ndate: 2026-04-10\n---\nOnly links to [[Design]] now.",
                encoding="utf-8",
            )
            kg.populate_from_note(note, vault_path)

            after = {t.object for t in kg.query_relation("Auth", "links_to")}
            assert "Design" in after
            assert "Backup" not in after, (
                "Stale links_to triple for the removed [[Backup]] wikilink "
                "should have been cleaned up on re-populate"
            )

    def test_populate_from_vault_uses_single_transaction(self):
        """P1.6: populate_from_vault should hit the database with a single
        connection/commit rather than one per note."""
        from unittest.mock import patch
        from pathlib import Path as _Path
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = _Path(tmpdir)
            active = vault_path / "work" / "active"
            active.mkdir(parents=True, exist_ok=True)
            for i in range(6):
                (active / f"note-{i}.md").write_text(
                    f"---\ndate: 2026-04-10\n---\nSee [[Target{i}]]",
                    encoding="utf-8",
                )

            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            with patch.object(kg, "_conn", wraps=kg._conn) as conn_spy:
                kg.populate_from_vault(vault_path)
            # One connection for all 6 notes (plus the cleanup/upsert) —
            # not one per note.
            assert conn_spy.call_count == 1, (
                f"Expected populate_from_vault to use 1 connection, "
                f"got {conn_spy.call_count}"
            )


class TestOrphanAndBrainFixes:
    """Test orphan detection and brain note counting fixes."""

    def test_orphans_resolve_wikilinks_by_filename(self):
        """Wikilinks should resolve by filename even in subdirectories."""
        from ompa import Vault
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            # Create notes in subdirectories
            skills = Path(tmpdir) / "work" / "active"
            skills.mkdir(parents=True, exist_ok=True)
            (skills / "Auth.md").write_text(
                "---\ndate: 2026-04-10\n---\nSee [[Design]].",
                encoding="utf-8",
            )
            (skills / "Design.md").write_text(
                "---\ndate: 2026-04-10\n---\nDesign doc for [[Auth]].",
                encoding="utf-8",
            )
            orphans = vault.find_orphans()
            orphan_names = [o.path.stem for o in orphans]
            # Auth and Design link to each other — neither should be orphaned
            assert "Auth" not in orphan_names
            assert "Design" not in orphan_names

    def test_orphans_with_md_extension_in_wikilink(self):
        """Wikilinks like [[SOUL.md]] should resolve correctly."""
        from ompa import Vault
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            brain = Path(tmpdir) / "brain"
            brain.mkdir(parents=True, exist_ok=True)
            (brain / "SOUL.md").write_text(
                "---\ndate: 2026-04-10\n---\nSoul content.",
                encoding="utf-8",
            )
            (brain / "Index.md").write_text(
                "---\ndate: 2026-04-10\n---\nLinks: [[SOUL.md]]",
                encoding="utf-8",
            )
            orphans = vault.find_orphans()
            orphan_names = [o.path.stem for o in orphans]
            # SOUL is linked from Index, so not an orphan
            assert "SOUL" not in orphan_names

    def test_brain_notes_count_by_frontmatter_wing(self):
        """Notes with wing=brain in frontmatter should count as brain notes."""
        from ompa import Vault
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            # Put notes outside brain/ folder but with wing: brain
            root = Path(tmpdir)
            (root / "SOUL.md").write_text(
                "---\ndate: 2026-04-10\nwing: brain\n---\nSoul content.",
                encoding="utf-8",
            )
            (root / "IDENTITY.md").write_text(
                "---\ndate: 2026-04-10\nwing: brain\n---\nIdentity.",
                encoding="utf-8",
            )
            (root / "SKILLS.md").write_text(
                "---\ndate: 2026-04-10\nwing: work\n---\nSkills.",
                encoding="utf-8",
            )
            stats = vault.get_stats()
            # Should count the 2 wing=brain notes
            assert stats["brain_notes"] >= 2

    def test_brain_notes_count_in_brain_folder(self):
        """Notes in brain/ folder should always count."""
        from ompa import Vault
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            brain = Path(tmpdir) / "brain"
            brain.mkdir(parents=True, exist_ok=True)
            (brain / "North Star.md").write_text(
                "---\ndate: 2026-04-10\n---\nGoals.",
                encoding="utf-8",
            )
            (brain / "Decisions.md").write_text(
                "---\ndate: 2026-04-10\n---\nKey decisions.",
                encoding="utf-8",
            )
            stats = vault.get_stats()
            assert stats["brain_notes"] >= 2

    def test_orphan_stats_match_find_orphans(self):
        """get_stats() orphan count should match find_orphans() length."""
        from ompa import Vault
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            brain = Path(tmpdir) / "brain"
            brain.mkdir(parents=True, exist_ok=True)
            (brain / "A.md").write_text(
                "---\ndate: 2026-04-10\n---\nSee [[B]].",
                encoding="utf-8",
            )
            (brain / "B.md").write_text(
                "---\ndate: 2026-04-10\n---\nSee [[A]].",
                encoding="utf-8",
            )
            (brain / "Lonely.md").write_text(
                "---\ndate: 2026-04-10\n---\nNo links here.",
                encoding="utf-8",
            )
            stats = vault.get_stats()
            orphans = vault.find_orphans()
            assert stats["orphans"] == len(orphans)
            # A and B link to each other, Lonely has no incoming links
            orphan_names = [o.path.stem for o in orphans]
            assert "Lonely" in orphan_names
            assert "A" not in orphan_names


class TestDualVault:
    """Test dual-vault architecture."""

    def test_single_vault_backward_compat(self):
        """Single-vault init should still work identically."""
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            assert ao.is_dual_vault is False
            assert ao.personal_vault is None
            result = ao.session_start()
            assert result.success

    def test_dual_vault_init(self):
        """Dual-vault should create both vault structures."""
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            assert ao.is_dual_vault is True
            assert ao.vault is not None
            assert ao.personal_vault is not None
            assert (shared / "brain").is_dir()
            assert (personal / "brain").is_dir()

    def test_auto_classify_shared(self):
        """Team decisions should route to shared vault."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig()
        target = config.classify_content(
            "We decided to use Postgres for the database",
            tags=["@team", "decision"],
        )
        assert target == VaultTarget.SHARED

    def test_auto_classify_personal(self):
        """Content with credentials should route to personal vault."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig()
        target = config.classify_content(
            "My API key is sk-abc123xyz",
            tags=["api-keys"],
        )
        assert target == VaultTarget.PERSONAL

    def test_auto_classify_personal_tag(self):
        """@private tag should route to personal."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig()
        target = config.classify_content(
            "Some random note",
            tags=["@private"],
        )
        assert target == VaultTarget.PERSONAL

    def test_auto_classify_folder_rules(self):
        """Folder-based routing should work."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig()
        assert (
            config.classify_content("Note", file_path="brain/North Star.md")
            == VaultTarget.SHARED
        )
        assert (
            config.classify_content("Note", file_path="personal/config.md")
            == VaultTarget.PERSONAL
        )

    # ------------------------------------------------------------------
    # P2.5 — indicator regex must use word boundaries, not substring match
    # ------------------------------------------------------------------

    def test_indicator_no_false_positive_sk_prefix(self):
        """`sk-` indicator must NOT fire on words that merely contain `sk-`
        as a substring (e.g. "desk-work")."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.SHARED)
        target = config.classify_content(
            "Bought a standing desk-work setup over the weekend.",
        )
        # No personal indicators, no shared indicators, no folder — falls to
        # default (shared), proving `sk-` did NOT match `desk-work`.
        assert target == VaultTarget.SHARED

    def test_indicator_sk_prefix_still_fires_on_real_key(self):
        """`sk-` must still catch an actual OpenAI-style secret."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.SHARED)
        target = config.classify_content(
            "Config: OPENAI_API_KEY=sk-AbCdEf1234567890xyz",
        )
        assert target == VaultTarget.PERSONAL

    def test_indicator_no_false_positive_token_in_tokenize(self):
        """`token` indicator must NOT fire on `tokenize` / `tokenized`."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.SHARED)
        target = config.classify_content(
            "We tokenize the input using a BPE scheme; the tokenizer is fast.",
        )
        assert target == VaultTarget.SHARED

    def test_indicator_token_still_fires_whole_word(self):
        """`token` must still match when the word stands alone."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.SHARED)
        target = config.classify_content(
            "I stored the auth token in a local file.",
        )
        assert target == VaultTarget.PERSONAL

    def test_indicator_akia_case_sensitive_prefix(self):
        """`AKIA` indicator is uppercase-only; lowercase `akia` substring
        (as in a random word) must NOT fire."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.SHARED)
        # `akia` appears lowercased and inside another word — must not match.
        assert (
            config.classify_content("The blackiawave was long and slow.")
            == VaultTarget.SHARED
        )
        # Real AWS-style key — must fire.
        assert (
            config.classify_content("AWS key rotated: AKIAIOSFODNN7EXAMPLE today.")
            == VaultTarget.PERSONAL
        )

    def test_indicator_decision_no_false_positive_on_decisions(self):
        """`decision` indicator uses \\b boundaries so it does not match
        `decisions` or `decisioned` — users who want plural/variant forms
        should add them to the indicator list explicitly."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.PERSONAL)
        # "decisions" (plural) — no match, falls through to default.
        target = config.classify_content(
            "Made several decisions over lunch.",
        )
        assert target == VaultTarget.PERSONAL

    def test_write_to_shared(self):
        """write() with vault='shared' should write to shared vault."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            result = ao.write(
                "We agreed to use PostgreSQL",
                vault="shared",
                tags=["decision"],
            )
            assert result["vault"] == "shared"
            assert Path(result["path"]).exists()

    def test_write_to_personal(self):
        """write() with vault='personal' should write to personal vault."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            result = ao.write(
                "My secret API key: sk-12345",
                vault="personal",
                tags=["api-keys"],
            )
            assert result["vault"] == "personal"
            assert Path(result["path"]).exists()

    def test_write_auto_classify(self):
        """write() without vault= should auto-classify."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                isolation_mode="permissive",
                enable_semantic=False,
            )
            # Content with API key should go to personal
            result = ao.write("password: hunter2")
            assert result["vault"] == "personal"

    def test_export_to_shared(self):
        """export_to_shared should copy note to shared vault."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            # Create a personal note
            note_dir = personal / "brain"
            note_dir.mkdir(parents=True, exist_ok=True)
            (note_dir / "idea.md").write_text(
                "---\ndate: 2026-04-11\n---\nRefactor the auth layer.",
                encoding="utf-8",
            )
            # Export (with confirm=False to actually export)
            result = ao.export_to_shared("brain/idea.md", confirm=False)
            assert result["success"] is True
            assert (shared / "brain" / "idea.md").exists()

    def test_export_sanitizes_content(self):
        """export_to_shared should redact credentials."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            note_dir = personal / "brain"
            note_dir.mkdir(parents=True, exist_ok=True)
            (note_dir / "keys.md").write_text(
                "---\ndate: 2026-04-11\n---\nAPI key: sk-abcdefghijklmnopqrstuvwxyz",
                encoding="utf-8",
            )
            result = ao.export_to_shared("brain/keys.md", confirm=False, sanitize=True)
            assert result["success"] is True
            exported = (shared / "brain" / "keys.md").read_text(encoding="utf-8")
            assert "sk-abcdefghijklmnopqrstuvwxyz" not in exported
            assert "[REDACTED]" in exported

    def test_sanitize_content_covers_broad_secret_set(self):
        """P0.9: _sanitize_content must catch common cloud/service credentials."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(
                shared_vault_path=Path(tmpdir) / "s",
                personal_vault_path=Path(tmpdir) / "p",
                enable_semantic=False,
            )
            # Fixtures intentionally built from prefix + low-entropy "A" bodies
            # so they exercise the regexes without tripping git-hosting secret
            # scanners. Prefix strings are split into two adjacent literals so
            # a naive regex scan of this file won't hit a canonical-format
            # secret either.
            secrets = {
                "openai": ("sk-" "proj-") + "A" * 36,
                "anthropic": ("sk-" "ant-") + "A" * 36,
                "aws_akia": ("AK" "IA") + "A" * 16,
                "aws_asia": ("AS" "IA") + "A" * 16,
                "github_classic": ("gh" "p_") + "A" * 36,
                "github_oauth": ("gh" "o_") + "A" * 36,
                "github_fg": ("github_" "pat_") + "A" * 22,
                "gitlab": ("gl" "pat-") + "A" * 20,
                "slack_bot": ("xox" "b-") + "A" * 20,
                "google": ("AI" "za") + "A" * 35,
                "stripe_live": ("sk_" "live_") + "A" * 20,
                "stripe_restricted": ("rk_" "live_") + "A" * 20,
                "jwt": (
                    ("ey" "J") + "A" * 15 + "."
                    + ("ey" "J") + "A" * 15 + "."
                    + "A" * 20
                ),
                "azure": "AccountKey=" + "A" * 60,
                "mongodb": "mongodb+srv://user:pw@cluster.example.net/db",
                "postgres": "postgresql://user:pw@db.example.com:5432/app",
                "pem": (
                    "-----BEGIN RSA PRIVATE KEY-----\n"
                    + "A" * 20
                    + "\n-----END RSA PRIVATE KEY-----"
                ),
                "generic_password": "password: hunter2",
                "generic_bearer": "Bearer=abc123xyz",
            }

            for label, secret in secrets.items():
                sanitized = ao._sanitize_content(f"note with {secret} inside")
                if label in ("generic_password", "generic_bearer"):
                    # generic KV redaction preserves the key, redacts value
                    assert "[REDACTED]" in sanitized, label
                else:
                    assert "[REDACTED]" in sanitized, label
                    # The literal secret body should not survive.
                    # For JWT, the full token string is redacted.
                    assert secret not in sanitized, label

    def test_sanitize_content_preserves_safe_text(self):
        """Guard against over-redaction of ordinary words."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(
                shared_vault_path=Path(tmpdir) / "s",
                personal_vault_path=Path(tmpdir) / "p",
                enable_semantic=False,
            )
            text = (
                "The desk-work document mentions a token review. "
                "We stored secrets in a vault. Not a real password."
            )
            sanitized = ao._sanitize_content(text)
            # Plain prose about "secret" and "token" without a key=value
            # pattern should pass through unchanged.
            assert "desk-work" in sanitized
            assert "token review" in sanitized

    def test_import_to_personal(self):
        """import_to_personal should copy note to personal vault."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            # Create a shared note
            note_dir = shared / "brain"
            note_dir.mkdir(parents=True, exist_ok=True)
            (note_dir / "spec.md").write_text(
                "---\ndate: 2026-04-11\n---\nAPI spec.",
                encoding="utf-8",
            )
            result = ao.import_to_personal("brain/spec.md", link_back=True)
            assert result["success"] is True
            imported = (personal / "brain" / "spec.md").read_text(encoding="utf-8")
            assert "Imported from shared" in imported

    def test_cross_vault_search(self):
        """search() with vaults=['shared','personal'] should search both."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            # Create notes in both vaults with searchable names
            (shared / "brain").mkdir(parents=True, exist_ok=True)
            (shared / "brain" / "Auth-Team.md").write_text(
                "---\ndate: 2026-04-11\n---\nTeam auth decision.",
                encoding="utf-8",
            )
            (personal / "brain").mkdir(parents=True, exist_ok=True)
            (personal / "brain" / "Auth-Private.md").write_text(
                "---\ndate: 2026-04-11\n---\nMy private auth notes.",
                encoding="utf-8",
            )
            # Search both (by filename match since semantic is off)
            results = ao.search("Auth", vaults=["shared", "personal"])
            paths = [r.path for r in results]
            assert any("Auth-Team" in p for p in paths)
            assert any("Auth-Private" in p for p in paths)

    def test_isolation_strict_export_preview(self):
        """In strict mode, export with confirm=True should return preview."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                isolation_mode="strict",
                enable_semantic=False,
            )
            note_dir = personal / "brain"
            note_dir.mkdir(parents=True, exist_ok=True)
            (note_dir / "draft.md").write_text(
                "---\ndate: 2026-04-11\n---\nDraft idea.",
                encoding="utf-8",
            )
            result = ao.export_to_shared("brain/draft.md", confirm=True)
            assert result["action"] == "preview"
            assert "Draft idea" in result["preview"]

    def test_mcp_write_tool(self):
        """MCP ao_write tool should work."""
        from ompa.mcp_server import handle_call_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_call_tool(
                "ao_write",
                {"content": "Test note content", "vault_path": tmpdir},
            )
            assert "vault" in result
            assert "path" in result

    def test_classifier_vault_target(self):
        """Classifier should suggest vault targets."""
        from ompa import MessageClassifier

        c = MessageClassifier()
        assert c.classify_vault_target("We decided to use Postgres") == "shared"
        assert c.classify_vault_target("random stuff") == "ambiguous"

    def test_export_to_shared_path_traversal_blocked(self):
        """export_to_shared() should reject note paths outside personal vault."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            result = ao.export_to_shared("../../outside.md", confirm=False)
            assert result["success"] is False
            assert "Invalid note_path" in result["error"]

    def test_import_to_personal_path_traversal_blocked(self):
        """import_to_personal() should reject note paths outside shared vault."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            result = ao.import_to_personal("../../outside.md")
            assert result["success"] is False
            assert "Invalid note_path" in result["error"]

    def test_write_path_traversal_blocked(self):
        """write() should reject file paths that escape the vault."""
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            with pytest.raises(ValueError, match="Path traversal blocked"):
                ao.write("evil content", file_path="../../etc/passwd")

    def test_dual_vault_sync(self):
        """sync() should sync both vaults in dual mode."""
        from ompa import Ompa
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            # Create notes in both
            (shared / "brain").mkdir(parents=True, exist_ok=True)
            (shared / "brain" / "S.md").write_text(
                "---\ndate: 2026-04-11\n---\n[[Link]].",
                encoding="utf-8",
            )
            (personal / "brain").mkdir(parents=True, exist_ok=True)
            (personal / "brain" / "P.md").write_text(
                "---\ndate: 2026-04-11\n---\n[[PLink]].",
                encoding="utf-8",
            )
            result = ao.sync()
            assert result["kg_triples"] > 0
            assert "personal_kg_triples" in result
            assert result["personal_kg_triples"] > 0


class TestSemanticIndex:
    """Test semantic index behavior."""

    def test_index_vault_initializes_model(self):
        """index_vault should lazy-init the model if not yet initialized."""
        from ompa.semantic import SemanticIndex
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            idx = SemanticIndex(index_path=Path(tmpdir) / "idx")
            assert idx._initialized is False

            # Monkeypatch _init_model to track that it was called
            called = []

            def fake_init():
                called.append(True)
                idx._initialized = True
                idx._model = "fake"

            idx._init_model = fake_init

            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            (vault / "test.md").write_text("Hello world content here", encoding="utf-8")

            count = idx.index_vault(vault)
            assert len(called) == 1  # _init_model was triggered
            assert count >= 1

    # ------------------------------------------------------------------
    # P1.2 — v2 binary storage format (chunks meta + .npy embedding matrix)
    # ------------------------------------------------------------------

    def _make_stub_index(self, tmpdir):
        """Helper: build a SemanticIndex with a deterministic stub model that
        avoids downloading sentence-transformers during tests.

        The stub encodes each input string to a tiny 4-dim vector based on
        character counts — enough for meaningful cosine similarity without
        any external model dependency.
        """
        from ompa.semantic import SemanticIndex
        from pathlib import Path
        import numpy as np

        class StubModel:
            def encode(self, texts, convert_to_numpy=True, show_progress_bar=False):
                # Accept a string or a list of strings.
                single = isinstance(texts, str)
                items = [texts] if single else list(texts)
                out = np.zeros((len(items), 4), dtype=np.float32)
                for i, t in enumerate(items):
                    tl = t.lower()
                    out[i, 0] = tl.count("a") + 0.1
                    out[i, 1] = tl.count("e") + 0.1
                    out[i, 2] = tl.count("o") + 0.1
                    out[i, 3] = len(tl) % 7 + 0.1
                if single:
                    return out[0]
                return out

        idx = SemanticIndex(
            index_path=Path(tmpdir) / "idx",
            embedding_dim=4,
        )
        idx._model = StubModel()
        idx._initialized = True
        return idx

    def test_v2_binary_format_roundtrip(self):
        """After index_file + save_index, we should see meta.json + .npy on disk,
        and load_index should rebuild the in-memory state identically."""
        import numpy as np

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            note = vault / "notes.md"
            note.write_text(
                "Alpha beta gamma " * 30 + "\n\n" + "delta epsilon zeta " * 30,
                encoding="utf-8",
            )

            idx = self._make_stub_index(tmpdir)
            idx.index_file(note)
            idx.save_index()

            meta_file = idx.index_path / "semantic_index.meta.json"
            emb_file = idx.index_path / "semantic_index.embeddings.npy"
            legacy_file = idx.index_path / "semantic_index.json"

            assert meta_file.exists(), "v2 meta file should be written"
            assert emb_file.exists(), "v2 embedding matrix should be written"
            assert not legacy_file.exists(), "legacy JSON file should not exist"

            original_chunks = list(idx.chunks)
            original_emb = np.asarray(idx.embeddings, dtype=np.float32).copy()
            assert original_emb.ndim == 2
            assert original_emb.shape[0] == len(original_chunks)

            # Reload into a fresh index and compare.
            fresh = self._make_stub_index(tmpdir)
            assert fresh.load_index() is True
            assert fresh.chunks == original_chunks
            np.testing.assert_allclose(
                np.asarray(fresh.embeddings, dtype=np.float32),
                original_emb,
                rtol=1e-6,
                atol=1e-6,
            )

    def test_legacy_json_format_is_migrated_on_load(self):
        """Legacy single-JSON indexes (per-chunk embedding lists) must be
        auto-migrated to the v2 split format on first load, and the old
        file deleted so subsequent loads are fast."""
        import json as _json
        import numpy as np

        with tempfile.TemporaryDirectory() as tmpdir:
            idx = self._make_stub_index(tmpdir)
            idx.index_path.mkdir(parents=True, exist_ok=True)

            legacy_chunks = [
                {
                    "hash": "abc123",
                    "path": str(Path(tmpdir) / "a.md"),
                    "chunk_index": 0,
                    "text": "hello world from a",
                    "embedding": [0.1, 0.2, 0.3, 0.4],
                },
                {
                    "hash": "def456",
                    "path": str(Path(tmpdir) / "b.md"),
                    "chunk_index": 0,
                    "text": "another chunk from b",
                    "embedding": [0.5, 0.6, 0.7, 0.8],
                },
            ]
            legacy_path = idx.index_path / "semantic_index.json"
            legacy_path.write_text(
                _json.dumps(
                    {"model": "stub", "embedding_dim": 4, "chunks": legacy_chunks}
                ),
                encoding="utf-8",
            )

            assert idx.load_index() is True
            # Metadata stripped of per-chunk `embedding` field.
            assert all("embedding" not in c for c in idx.chunks)
            assert [c["hash"] for c in idx.chunks] == ["abc123", "def456"]
            # Embeddings now live in a numpy matrix.
            emb = np.asarray(idx.embeddings, dtype=np.float32)
            assert emb.shape == (2, 4)
            np.testing.assert_allclose(
                emb[0], np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32), atol=1e-6
            )
            # Legacy file deleted, v2 files written.
            assert not legacy_path.exists()
            assert (idx.index_path / "semantic_index.meta.json").exists()
            assert (idx.index_path / "semantic_index.embeddings.npy").exists()

    def test_index_file_uses_batched_encode(self):
        """A multi-chunk file should trigger a SINGLE batched encode call,
        not one encode per chunk (P1.2 goal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = self._make_stub_index(tmpdir)

            call_count = {"n": 0, "batch_sizes": []}
            original_encode = idx._model.encode

            def spy_encode(texts, **kwargs):
                call_count["n"] += 1
                if isinstance(texts, list):
                    call_count["batch_sizes"].append(len(texts))
                else:
                    call_count["batch_sizes"].append(1)
                return original_encode(texts, **kwargs)

            idx._model.encode = spy_encode

            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            # Force 3+ chunks by making a long note (chunk_size = 512 words).
            big_note = vault / "big.md"
            big_note.write_text(
                ("word " * 600) + "\n" + ("other " * 600) + "\n" + ("more " * 600),
                encoding="utf-8",
            )
            idx.index_file(big_note)

            assert call_count["n"] == 1, (
                f"Expected 1 batched encode call for the whole file, got "
                f"{call_count['n']} (batches: {call_count['batch_sizes']})"
            )
            # All chunks were encoded together.
            assert call_count["batch_sizes"][0] >= 2

    # ------------------------------------------------------------------
    # P1.3 — best-per-path dedup (up to `limit` distinct files)
    # ------------------------------------------------------------------

    def test_search_returns_up_to_limit_distinct_paths(self):
        """If a single file contributes many top-scoring chunks, the search
        must still surface other files up to `limit`, not be dominated by
        one path (prior bug: sort-then-dedup could return <limit results)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = self._make_stub_index(tmpdir)

            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            # File A — 3 chunks, each long enough to survive the 20-char filter.
            # Its chunks will happen to score high because they contain the
            # query token "alpha".
            (vault / "a.md").write_text(
                ("alpha " * 520) + "\n" + ("alpha " * 520) + "\n" + ("alpha " * 520),
                encoding="utf-8",
            )
            (vault / "b.md").write_text("alpha " * 100, encoding="utf-8")
            (vault / "c.md").write_text("alpha " * 100, encoding="utf-8")

            idx.index_file(vault / "a.md")
            idx.index_file(vault / "b.md")
            idx.index_file(vault / "c.md")

            results = idx.search("alpha", limit=3, hybrid=True)
            paths = [r.path for r in results]

            # Each result must be a distinct path.
            assert len(paths) == len(set(paths)), (
                f"Expected distinct paths per result, got: {paths}"
            )
            # We should see all three files (not just a.md repeated).
            assert len(results) == 3
            assert {Path(p).name for p in paths} == {"a.md", "b.md", "c.md"}

    def test_remove_file_rebuilds_embedding_matrix(self):
        """Removing a file should drop exactly its rows from the embedding
        matrix, leaving the remaining rows row-aligned with self.chunks."""
        import numpy as np

        with tempfile.TemporaryDirectory() as tmpdir:
            idx = self._make_stub_index(tmpdir)

            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            a = vault / "a.md"
            b = vault / "b.md"
            a.write_text("alpha beta gamma " * 30, encoding="utf-8")
            b.write_text("delta epsilon zeta " * 30, encoding="utf-8")

            idx.index_file(a)
            idx.index_file(b)
            before_total = len(idx.chunks)
            before_b = sum(1 for c in idx.chunks if c["path"] == str(b))
            assert before_b >= 1

            assert idx.remove_file(a) is True

            assert all(c["path"] != str(a) for c in idx.chunks)
            assert len(idx.chunks) == before_total - (before_total - before_b)
            # Row-alignment invariant.
            emb = np.asarray(idx.embeddings, dtype=np.float32)
            assert emb.shape[0] == len(idx.chunks)


class TestSearchFilter:
    """P0.8: wing/room filter on search must not silently mask empty results."""

    def test_search_vault_empty_filter_returns_empty(self):
        """When wing/room filter excludes all results, return [] — do NOT fall
        back to unfiltered results (prior bug)."""
        from ompa import Ompa
        from ompa.semantic import SearchResult

        class StubSemantic:
            def search(self, query, limit, hybrid):
                # Two results, neither under wing "work" / room "payments".
                return [
                    SearchResult(
                        path="brain/north-star.md",
                        content_excerpt="...",
                        score=0.9,
                        match_type="semantic",
                    ),
                    SearchResult(
                        path="org/people/alice.md",
                        content_excerpt="...",
                        score=0.8,
                        match_type="semantic",
                    ),
                ]

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            # Bypass real semantic, invoke _search_vault directly with a stub.
            results = ao._search_vault(
                ao.vault,
                StubSemantic(),
                "anything",
                limit=5,
                hybrid=True,
                wing="work",
                room="payments",
            )
            # Must honor filter: no result has "work" AND "payments" in path.
            assert results == []

    def test_search_vault_filter_keeps_matches(self):
        """Sanity: matching filter returns matches."""
        from ompa import Ompa
        from ompa.semantic import SearchResult

        class StubSemantic:
            def search(self, query, limit, hybrid):
                return [
                    SearchResult(
                        path="work/active/payments.md",
                        content_excerpt="...",
                        score=0.9,
                        match_type="semantic",
                    ),
                    SearchResult(
                        path="brain/north-star.md",
                        content_excerpt="...",
                        score=0.8,
                        match_type="semantic",
                    ),
                ]

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            results = ao._search_vault(
                ao.vault,
                StubSemantic(),
                "x",
                limit=5,
                hybrid=True,
                wing="work",
                room="payments",
            )
            assert len(results) == 1
            assert "payments" in results[0].path
