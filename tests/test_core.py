"""Tests for core."""

import tempfile


class TestOmpa:
    """Test core Ompa integration."""

    def test_session_start(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            result = ao.session_start()
            assert result.success is True

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
            assert result.success is True

    def test_backward_compat_alias(self):
        """Verify AgnosticObsidian still works as an alias."""
        from ompa import AgnosticObsidian, Ompa

        assert AgnosticObsidian is Ompa

    def test_python_m_ompa_entrypoint(self):
        """Verify __main__.py exists for python -m ompa."""
        import importlib

        spec = importlib.util.find_spec("ompa.__main__")
        assert spec is not None



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
