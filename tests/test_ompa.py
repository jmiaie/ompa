"""
OMPA test suite.
Run: PYTHONPATH=. pytest tests/ -v
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
            p.add_hall("Orion", "auth-migration", "hall_facts", content="Team chose Clerk")
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
            kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01", valid_to="2025-12-01")
            kg.add_triple("Kai", "works_on", "OMPA", valid_from="2026-01-01")
            # Query as of date when Orion was current
            triples = kg.query_entity("Kai", as_of="2025-09-01")
            assert any(t.object == "Orion" for t in triples)
            # Query as of date when only OMPA is current
            triples = kg.query_entity("Kai", as_of="2026-03-01")
            assert all(t.object == "OMPA" for t in triples)

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
