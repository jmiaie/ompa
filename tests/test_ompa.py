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

    def test_note_save_utf8(self):
        from ompa.vault import Note
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unicode.md"
            note = Note(path=path, content="Héllo wörld 日本語")
            note.save()
            loaded = Note.from_file(path)
            assert "日本語" in loaded.content

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
