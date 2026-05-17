"""Tests for ompa.knowledge_graph — Temporal KG."""

import os
import tempfile
from pathlib import Path


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


class TestKGPopulation:
    """Test KG auto-population from vault notes."""

    def test_populate_from_note_wikilinks(self):
        """Wikilinks should create links_to triples."""
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
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

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            vault_path = Path(tmpdir)
            note_path = vault_path / "brain" / "Tagged.md"
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(
                "---\ndate: 2026-04-10\ntags: [auth, security]\n---\nContent here.",
                encoding="utf-8",
            )
            kg.populate_from_note(note_path, vault_path)
            triples = kg.query_entity("Tagged")
            tag_triples = [t for t in triples if t.predicate == "has_tag"]
            assert len(tag_triples) == 2
            tags = {t.object for t in tag_triples}
            assert "auth" in tags
            assert "security" in tags

    def test_populate_from_note_folders(self):
        """Notes should get in_folder triples from their directory."""
        from ompa import KnowledgeGraph

        with tempfile.TemporaryDirectory() as tmpdir:
            kg = KnowledgeGraph(db_path=os.path.join(tmpdir, "kg.sqlite3"))
            vault_path = Path(tmpdir)
            note_path = vault_path / "work" / "active" / "Auth.md"
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(
                "---\ndate: 2026-04-10\n---\nAuth work note.",
                encoding="utf-8",
            )
            kg.populate_from_note(note_path, vault_path)
            triples = kg.query_entity("Auth")
            folder_triples = [t for t in triples if t.predicate == "in_folder"]
            assert len(folder_triples) == 1
            assert folder_triples[0].object == "work"

    def test_populate_from_vault(self):
        """populate_from_vault should scan all notes."""
        from ompa import KnowledgeGraph

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

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
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
