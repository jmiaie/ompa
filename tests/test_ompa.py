"""
OMPA test suite.
Run: pytest tests/ -v
"""

import os
import tempfile
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

    def test_safe_resolve_prefix_collision_blocked(self):
        from pathlib import Path
        from ompa.vault import _safe_resolve

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = root / "vault"
            sibling = root / "vault-evil"
            base.mkdir()
            sibling.mkdir()

            with pytest.raises(ValueError, match="Path traversal blocked"):
                _safe_resolve(base, "../vault-evil/pwn.md")

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

    def test_write_path_traversal_blocked(self):
        """write() should reject paths that escape the target vault."""
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
            with pytest.raises(ValueError, match="Path traversal blocked"):
                ao.write("escaped", vault="shared", file_path="../escape.md")

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

            result = ao.import_to_personal("../../outside.md", link_back=True)
            assert result["success"] is False
            assert "Invalid note_path" in result["error"]


class TestSemanticIndex:
    """Test semantic index lazy initialization behavior."""

    def test_index_vault_initializes_model(self, monkeypatch):
        """index_vault() should initialize the model before indexing."""
        from pathlib import Path
        from ompa.semantic import SemanticIndex

        class DummyEmbedding(list):
            def tolist(self):
                return list(self)

        class DummyModel:
            def encode(self, _text):
                return DummyEmbedding([0.1, 0.2, 0.3])

        def fake_init(self):
            self._model = DummyModel()
            self._initialized = True

        monkeypatch.setattr(SemanticIndex, "_init_model", fake_init)

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
            note = vault_path / "brain" / "Semantic.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(
                "This is a sufficiently long semantic indexing note with many words.",
                encoding="utf-8",
            )

            index = SemanticIndex(index_path=vault_path / ".palace" / "semantic_index")
            indexed = index.index_vault(vault_path)

            assert indexed >= 1
            assert len(index.chunks) >= 1

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


# ---------------------------------------------------------------------------
# Regression: temporal history must survive re-assertion
# ---------------------------------------------------------------------------


class TestTemporalHistory:
    """Re-asserting an invalidated fact must not overwrite its earlier interval."""

    def _kg(self, tmpdir):
        from ompa import KnowledgeGraph

        return KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))

    @staticmethod
    def _objects(kg, entity, as_of):
        return {t.object for t in kg.query_entity(entity, as_of=as_of) if t.predicate == "works_on"}

    def test_reassert_after_invalidate_preserves_earlier_interval(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            kg = self._kg(tmpdir)
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2020-01-01")
            kg.invalidate("Kai", "works_on", "Orion", ended="2020-12-31")
            kg.add_triple("Kai", "works_on", "Atlas", valid_from="2021-01-01")
            kg.invalidate("Kai", "works_on", "Atlas", ended="2021-12-31")
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2022-06-01")  # back on Orion

            assert self._objects(kg, "Kai", "2020-06-15") == {"Orion"}
            assert self._objects(kg, "Kai", "2021-06-15") == {"Atlas"}
            assert self._objects(kg, "Kai", "2022-01-15") == set()  # gap between stints
            assert self._objects(kg, "Kai", "2023-01-01") == {"Orion"}

            orion = [e for e in kg.timeline("Kai") if e["object"] == "Orion"]
            assert [(e["date"], e["end_date"]) for e in orion] == [
                ("2020-01-01", "2020-12-31"),
                ("2022-06-01", None),
            ]

    def test_repeated_assertion_of_current_fact_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            kg = self._kg(tmpdir)
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2024-01-01")
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2024-03-01")
            kg.add_triple("Kai", "works_on", "Orion")  # defaults to today
            rows = kg.query_relation("Kai", "works_on")
            assert len(rows) == 1
            assert rows[0].valid_from == "2024-01-01" and rows[0].valid_to is None

    def test_overlapping_closed_intervals_merge(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            kg = self._kg(tmpdir)
            kg.add_triple(
                "Kai", "works_on", "Orion", valid_from="2020-01-01", valid_to="2020-06-30"
            )
            kg.add_triple(
                "Kai", "works_on", "Orion", valid_from="2020-06-01", valid_to="2020-12-31"
            )
            kg.add_triple(
                "Kai", "works_on", "Orion", valid_from="2023-01-01", valid_to="2023-02-01"
            )
            rows = sorted(kg.query_relation("Kai", "works_on"), key=lambda t: t.valid_from)
            assert [(t.valid_from, t.valid_to) for t in rows] == [
                ("2020-01-01", "2020-12-31"),
                ("2023-01-01", "2023-02-01"),
            ]

    def test_populate_from_note_twice_does_not_duplicate(self):
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = self._kg(tmpdir)
            note = Path(tmpdir) / "work" / "orion.md"
            note.parent.mkdir(parents=True)
            note.write_text("---\ntags: [decision]\n---\nSee [[Atlas]].\n", encoding="utf-8")
            kg.populate_from_note(note, Path(tmpdir))
            first = kg.stats()["triple_count"]
            kg.populate_from_note(note, Path(tmpdir))
            assert kg.stats()["triple_count"] == first

    def test_randomized_histories_point_in_time_exact(self):
        """Seeded property check: every point-in-time query matches ground truth."""
        import random
        from datetime import date, timedelta

        for seed in range(5):
            rng = random.Random(seed)
            with tempfile.TemporaryDirectory() as tmpdir:
                kg = self._kg(tmpdir)
                truth = []
                for e in range(10):
                    t, prev = date(2020, 1, 1) + timedelta(days=rng.randint(0, 50)), None
                    for _ in range(rng.randint(2, 5)):
                        obj = prev if prev and rng.random() < 0.5 else f"p{rng.randrange(4)}"
                        end = t + timedelta(days=rng.randint(10, 300))
                        kg.add_triple(f"e{e}", "works_on", obj, valid_from=t.isoformat())
                        kg.invalidate(f"e{e}", "works_on", obj, ended=end.isoformat())
                        truth.append((f"e{e}", obj, t, end))
                        prev, t = obj, end + timedelta(days=rng.randint(1, 60))
                for _ in range(200):
                    ent = f"e{rng.randrange(10)}"
                    q = date(2020, 1, 1) + timedelta(days=rng.randint(0, 2000))
                    expected = {o for (s, o, a, b) in truth if s == ent and a <= q <= b}
                    assert self._objects(kg, ent, q.isoformat()) == expected, (seed, ent, q)


# ---------------------------------------------------------------------------
# Regression: notes become searchable without a manual rebuild
# ---------------------------------------------------------------------------


class _Vec(list):
    """List with a numpy-style tolist(), so tests don't need numpy installed."""

    def tolist(self):
        return list(self)


class _BagOfWordsBackend:
    """Deterministic tiny embedding backend for tests (no model download, no numpy)."""

    def encode(self, text):
        v = _Vec([0.0] * 64)
        for w in text.lower().split():
            v[sum(map(ord, w)) % 64] += 1.0
        return v


class TestIndexFreshness:
    @staticmethod
    def _names(results):
        from pathlib import Path

        return [Path(r.path).name for r in results]

    def test_write_is_searchable_without_rebuild(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(vault_path=tmpdir, embedding_backend=_BagOfWordsBackend())
            ao.write("Initial note about kubernetes cluster upgrades.", file_path="work/a.md")
            assert "a.md" in self._names(ao.search("kubernetes", limit=3))  # loads index
            ao.write(
                "Zebra decision: we chose Postgres for the zebra service.",
                file_path="work/zebra.md",
            )
            assert self._names(ao.search("zebra postgres", limit=3))[0] == "zebra.md"

    def test_rewritten_note_reflects_new_content(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(vault_path=tmpdir, embedding_backend=_BagOfWordsBackend())
            ao.write("Billing runs on Braintree today.", file_path="work/billing.md")
            ao.write("Unrelated filler note about lunch menus.", file_path="work/lunch.md")
            ao.search("billing", limit=2)
            ao.write("Billing migrated to Stripe checkout.", file_path="work/billing.md")
            hits = ao.search("stripe checkout", limit=1)
            assert self._names(hits) == ["billing.md"]
            assert "Stripe" in hits[0].content_excerpt

    def test_external_edits_and_deletes_picked_up_on_search(self):
        import time
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(
                vault_path=tmpdir, embedding_backend=_BagOfWordsBackend(), index_refresh_interval=0
            )
            ao.write("Seed note about release trains.", file_path="work/seed.md")
            ao.search("release", limit=3)
            # Edited outside OMPA (e.g. in Obsidian)
            ext = Path(tmpdir) / "work" / "ext.md"
            ext.write_text("Quokka migration plan for the payments ledger.", encoding="utf-8")
            assert self._names(ao.search("quokka ledger", limit=1)) == ["ext.md"]
            time.sleep(0.01)
            ext.unlink()
            assert "ext.md" not in self._names(ao.search("quokka ledger", limit=3))

    def test_fresh_process_catches_up_from_saved_index(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(vault_path=tmpdir, embedding_backend=_BagOfWordsBackend())
            ao.write("Seed note about release trains.", file_path="work/seed.md")
            ao.search("release", limit=3)
            ao.stop()  # flushes the index
            # A writer without semantic enabled (like the MCP ao_write tool)
            Ompa(vault_path=tmpdir, enable_semantic=False).write(
                "Narwhal incident postmortem for the search cluster.", file_path="work/narwhal.md"
            )
            fresh = Ompa(vault_path=tmpdir, embedding_backend=_BagOfWordsBackend())
            assert self._names(fresh.search("narwhal postmortem", limit=1)) == ["narwhal.md"]

    def test_refresh_only_reembeds_changed_files(self):
        from pathlib import Path

        from ompa.semantic import SemanticIndex

        calls = []

        class Counting(_BagOfWordsBackend):
            def encode(self, text):
                calls.append(text)
                return super().encode(text)

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir)
            for i in range(5):
                (vault / f"n{i}.md").write_text(
                    f"note number {i} with enough words", encoding="utf-8"
                )
            idx = SemanticIndex(vault / ".palace" / "semantic_index", embedding_backend=Counting())
            assert idx.refresh(vault) == {"added": 5, "updated": 0, "removed": 0}
            calls.clear()
            assert idx.refresh(vault) == {"added": 0, "updated": 0, "removed": 0}
            assert calls == []
            (vault / "n2.md").write_text(
                "note number two rewritten with new words", encoding="utf-8"
            )
            assert idx.refresh(vault)["updated"] == 1
            assert len(calls) == 1

    def test_legacy_index_without_mtimes_still_refreshes(self):
        import json
        import os as _os
        from pathlib import Path

        from ompa.semantic import SemanticIndex

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir)
            (vault / "old.md").write_text("an old note about llamas and alpacas", encoding="utf-8")
            idx = SemanticIndex(
                vault / ".palace" / "semantic_index", embedding_backend=_BagOfWordsBackend()
            )
            # simulate a v1 JSON index written by OMPA <= 1.0.8 (no "files" mtimes)
            idx.index_path.mkdir(parents=True, exist_ok=True)
            f = idx.index_path / "semantic_index.json"
            text = (vault / "old.md").read_text()
            emb = list(_BagOfWordsBackend().encode(text))
            chunk = {"hash": "h", "path": str(vault / "old.md"), "chunk_index": 0}
            data = {"model": "m", "chunks": [{**chunk, "text": text, "embedding": emb}]}
            f.write_text(json.dumps(data))
            st = (vault / "old.md").stat()
            _os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 10_000_000))
            (vault / "new.md").write_text("a brand new note about capybaras", encoding="utf-8")

            idx2 = SemanticIndex(idx.index_path, embedding_backend=_BagOfWordsBackend())
            assert idx2.load_index()
            assert idx2.refresh(vault) == {"added": 1, "updated": 0, "removed": 0}


class TestConcurrentFactWrites:
    """add_triple's check-then-write must stay atomic under concurrent writers."""

    def test_concurrent_overlapping_reassertions_merge_to_one_interval(self):
        import random
        import threading
        from datetime import date, timedelta

        from ompa import KnowledgeGraph

        base = date(2020, 1, 1)
        # 40 overlapping closed intervals of one fact -> union is a single interval
        spans = [(base + timedelta(days=d), base + timedelta(days=d + 10)) for d in range(0, 200, 5)]
        random.Random(7).shuffle(spans)
        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "kg.sqlite3")
            KnowledgeGraph(db_path=db)
            errors = []

            def worker(chunk):
                kg = KnowledgeGraph(db_path=db)  # separate connection per writer
                for a, b in chunk:
                    try:
                        kg.add_triple("Kai", "works_on", "Orion", a.isoformat(), b.isoformat())
                    except Exception as e:  # pragma: no cover - surfaced below
                        errors.append(e)

            threads = [threading.Thread(target=worker, args=(spans[i::8],)) for i in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            assert errors == []
            rows = KnowledgeGraph(db_path=db).query_relation("Kai", "works_on")
            assert [(r.valid_from, r.valid_to) for r in rows] == [
                (base.isoformat(), (base + timedelta(days=205)).isoformat())
            ]

    def test_concurrent_distinct_facts_no_lost_writes(self):
        import threading

        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            db = os.path.join(tmpdir, "kg.sqlite3")
            kg = KnowledgeGraph(db_path=db)

            def worker(w):
                for i in range(100):
                    kg.add_triple(f"w{w}", "item", f"i{i}", valid_from="2024-01-01")
                    kg.add_triple(f"w{w}", "item", f"i{i}", valid_from="2024-01-01")  # idempotent

            threads = [threading.Thread(target=worker, args=(w,)) for w in range(8)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            for w in range(8):
                assert len(kg.query_relation(f"w{w}", "item")) == 100

    def test_overlap_lookup_uses_exact_fact_index(self):
        """Guard against O(fan-out) writes: the overlap probe must hit idx_triples_spo."""
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            conn = kg._get_connection()
            plan = conn.execute(
                """EXPLAIN QUERY PLAN SELECT 1 FROM triples
                   WHERE subject = ? AND predicate = ? AND object = ?
                     AND (valid_to IS NULL OR valid_to >= ?)
                     AND (? IS NULL OR valid_from IS NULL OR valid_from <= ?)
                   LIMIT 1""",
                ("Kai", "works_on", "Orion", "2024-01-01", None, None),
            ).fetchall()
            assert any("idx_triples_spo" in row[3] for row in plan)


# ---------------------------------------------------------------------------
# Search: resident compact index, FTS5 keyword search, hybrid fusion
# ---------------------------------------------------------------------------


class TestKeywordSearch:
    """Without the semantic extra, search must match note *content* (BM25 / FTS5)."""

    @staticmethod
    def _names(results):
        from pathlib import Path

        return [Path(r.path).name for r in results]

    def test_content_match_without_semantic(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(vault_path=tmpdir, enable_semantic=False)
            ao.write("We chose Postgres for the billing service.", file_path="work/db.md")
            ao.write("Lunch options near the office.", file_path="work/lunch.md")
            res = ao.search("postgres billing", limit=5)
            assert self._names(res)[:1] == ["db.md"]
            assert res[0].match_type == "keyword"

    def test_external_edit_and_delete_picked_up(self):
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(vault_path=tmpdir, enable_semantic=False, index_refresh_interval=0)
            note = Path(tmpdir) / "brain" / "ext.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text("the quokka migration plan", encoding="utf-8")
            assert "ext.md" in self._names(ao.search("quokka"))
            note.write_text("the wombat migration plan, longer now", encoding="utf-8")
            assert "ext.md" in self._names(ao.search("wombat"))
            assert "ext.md" not in self._names(ao.search("quokka"))
            note.unlink()
            assert "ext.md" not in self._names(ao.search("wombat"))

    def test_fts_syntax_in_query_is_safe(self):
        from ompa.keyword import KeywordIndex, _fts_query

        assert _fts_query('NEAR(a b) "x AND y* :col') == '"near" OR "a" OR "b" OR "x" OR "and" OR "y" OR "col"'
        assert _fts_query("!!! ???") == ""
        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

            idx = KeywordIndex(Path(tmpdir) / "k.sqlite3")
            (Path(tmpdir) / "a.md").write_text("alpha AND beta", encoding="utf-8")
            assert idx.update_file(Path(tmpdir) / "a.md")
            assert [Path(r.path).name for r in idx.search('alpha" OR NOT *')] == ["a.md"]
            assert idx.search("!!!") == []

    def test_bm25_prefers_rarer_terms(self):
        from pathlib import Path

        from ompa.keyword import KeywordIndex

        with tempfile.TemporaryDirectory() as tmpdir:
            d = Path(tmpdir)
            for i in range(5):
                (d / f"common{i}.md").write_text(f"meeting notes number {i}", encoding="utf-8")
            (d / "rare.md").write_text("meeting about the flamingo budget", encoding="utf-8")
            idx = KeywordIndex(d / ".palace" / "k.sqlite3")
            assert idx.refresh(d) == {"added": 6, "updated": 0, "removed": 0}
            assert idx.refresh(d) == {"added": 0, "updated": 0, "removed": 0}
            assert Path(idx.search("meeting flamingo", 3)[0].path).name == "rare.md"


class TestResidentSemanticIndex:
    def test_compact_float16_format_roundtrip(self):
        import json
        from pathlib import Path

        from ompa.semantic import META_FILE, VECTORS_FILE, SemanticIndex

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir)
            for i, w in enumerate(["llama", "alpaca", "capybara"]):
                (vault / f"n{i}.md").write_text(f"a note about the {w} {w} herd", encoding="utf-8")
            idx = SemanticIndex(vault / ".palace" / "si", embedding_backend=_BagOfWordsBackend())
            idx.index_vault(vault)
            idx.save_index()
            meta = json.loads((idx.index_path / META_FILE).read_text())
            assert meta["version"] == 2 and meta["count"] == 3 and meta["dim"] == 64
            assert (idx.index_path / VECTORS_FILE).stat().st_size == 3 * 64 * 2
            before = [(r.path, round(r.score, 3)) for r in idx.search("capybara herd", 3)]

            idx2 = SemanticIndex(idx.index_path, embedding_backend=_BagOfWordsBackend())
            assert idx2.load_index()
            # resident: queries never touch the index files again
            for f in idx.index_path.iterdir():
                f.unlink()
            after = [(r.path, round(r.score, 3)) for r in idx2.search("capybara herd", 3)]
            assert after == before
            assert Path(after[0][0]).name == "n2.md"

    def test_truncated_vector_file_is_rejected(self):
        from pathlib import Path

        from ompa.semantic import VECTORS_FILE, SemanticIndex

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir)
            (vault / "a.md").write_text("some reasonably long note text here", encoding="utf-8")
            idx = SemanticIndex(vault / ".palace" / "si", embedding_backend=_BagOfWordsBackend())
            idx.index_vault(vault)
            idx.save_index()
            vf = idx.index_path / VECTORS_FILE
            vf.write_bytes(vf.read_bytes()[:-2])
            idx2 = SemanticIndex(idx.index_path, embedding_backend=_BagOfWordsBackend())
            assert idx2.load_index() is False
            assert idx2.chunks == []

    def test_legacy_json_index_is_migrated(self):
        import json
        from pathlib import Path

        from ompa.semantic import LEGACY_FILE, META_FILE, SemanticIndex

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir)
            note = vault / "a.md"
            note.write_text("legacy note about otters and rivers", encoding="utf-8")
            si = vault / ".palace" / "si"
            si.mkdir(parents=True)
            emb = list(_BagOfWordsBackend().encode(note.read_text()))
            st = note.stat()
            (si / LEGACY_FILE).write_text(
                json.dumps(
                    {
                        "model": "m",
                        "chunks": [
                            {"hash": "h", "path": str(note), "chunk_index": 0,
                             "text": note.read_text(), "embedding": emb}
                        ],
                        "files": {str(note): [st.st_mtime_ns, st.st_size]},
                    }
                )
            )
            idx = SemanticIndex(si, embedding_backend=_BagOfWordsBackend())
            assert idx.load_index()
            assert idx.refresh(vault) == {"added": 0, "updated": 0, "removed": 0}
            assert Path(idx.search("otters", 1)[0].path).name == "a.md"
            idx.flush()
            assert (si / META_FILE).exists() and not (si / LEGACY_FILE).exists()

    def test_incremental_updates_match_full_rebuild(self):
        """Random add/edit/delete sequences: incremental index == fresh rebuild."""
        import random
        from pathlib import Path

        from ompa.semantic import SemanticIndex

        words = "red green blue cyan magenta yellow black white orange purple".split()
        rng = random.Random(20261020)
        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir) / "v"
            vault.mkdir()
            idx = SemanticIndex(Path(tmpdir) / "si", embedding_backend=_BagOfWordsBackend())
            for step in range(120):
                name = vault / f"n{rng.randrange(15)}.md"
                if name.exists() and rng.random() < 0.3:
                    name.unlink()
                    idx.remove_file(name)
                else:
                    n_words = rng.choice([5, 30, 700, 1200])  # 1-3 chunks
                    name.write_text(" ".join(rng.choice(words) for _ in range(n_words)))
                    idx.update_file(name, save=False)
            fresh = SemanticIndex(Path(tmpdir) / "si2", embedding_backend=_BagOfWordsBackend())
            fresh.index_vault(vault)
            key = lambda ix: sorted((c["path"], c["chunk_index"]) for c in ix.chunks)  # noqa: E731
            assert key(idx) == key(fresh)
            for q in ["red blue", "orange purple black", "cyan"]:
                got = [(r.path, round(r.score, 4)) for r in idx.search(q, 10, hybrid=False)]
                exp = [(r.path, round(r.score, 4)) for r in fresh.search(q, 10, hybrid=False)]
                assert sorted(got) == sorted(exp)

    def test_hybrid_fuses_keyword_and_semantic(self):
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(vault_path=tmpdir, embedding_backend=_BagOfWordsBackend())
            ao.write("Release checklist for the aardvark project launch.", file_path="work/a.md")
            ao.write("Unrelated note on gardening and tomatoes.", file_path="work/b.md")
            res = ao.search("aardvark launch", limit=2)
            assert Path(res[0].path).name == "a.md"
            assert res[0].match_type == "hybrid"
            sem = ao.search("aardvark launch", limit=2, hybrid=False)
            assert all(r.match_type == "semantic" for r in sem)

    def test_rrf(self):
        from ompa.semantic import SearchResult, fuse_rrf

        s = [SearchResult("a", "", 0.9, "semantic"), SearchResult("b", "", 0.5, "semantic")]
        k = [SearchResult("b", "", 7.0, "keyword"), SearchResult("c", "", 3.0, "keyword")]
        out = fuse_rrf(s, k, 3)
        assert [r.path for r in out] == ["b", "a", "c"]
        assert [r.match_type for r in out] == ["hybrid", "semantic", "keyword"]

    def test_sentence_transformer_loaded_once_per_process(self, monkeypatch):
        import sys
        import types
        from pathlib import Path

        from ompa import semantic

        loads = []

        class FakeST:
            def __init__(self, name):
                loads.append(name)

        fake = types.ModuleType("sentence_transformers")
        fake.SentenceTransformer = FakeST
        monkeypatch.setitem(sys.modules, "sentence_transformers", fake)
        monkeypatch.setattr(semantic, "_MODEL_CACHE", {})
        with tempfile.TemporaryDirectory() as tmpdir:
            a = semantic.SemanticIndex(Path(tmpdir) / "a", model_name="fake-model")
            b = semantic.SemanticIndex(Path(tmpdir) / "b", model_name="fake-model")
            assert a.model is b.model
        assert loads == ["fake-model"]


def test_keyword_reindex_replaces_rows_without_duplicates():
    from pathlib import Path

    from ompa.keyword import KeywordIndex

    with tempfile.TemporaryDirectory() as tmpdir:
        d = Path(tmpdir)
        idx = KeywordIndex(d / ".palace" / "k.sqlite3")
        for i in range(20):
            (d / f"n{i}.md").write_text(f"note {i} about heron", encoding="utf-8")
        idx.refresh(d)
        for i in range(0, 20, 2):
            assert idx.update_file(d / f"n{i}.md")  # re-index in place
        (d / "n1.md").unlink()
        assert idx.remove_file(d / "n1.md")
        assert not idx.remove_file(d / "n1.md")
        fts_rows = idx._conn.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0]
        assert len(idx) == fts_rows == 19
        assert len(idx.search("heron", 100)) == 19
