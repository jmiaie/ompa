"""Tests for ompa.core — Ompa integration."""

import tempfile


class TestOmpa:
    """Test core Ompa integration."""

    def test_session_start(self):
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            result = ao.session_start()
            assert result.success

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
            assert result.success

    def test_backward_compat_alias(self):
        """Verify AgnosticObsidian still works as an alias."""
        from ompa import AgnosticObsidian, Ompa

        assert AgnosticObsidian is Ompa

    def test_python_m_ompa_entrypoint(self):
        """Verify __main__.py exists for python -m ompa."""
        import importlib

        spec = importlib.util.find_spec("ompa.__main__")
        assert spec is not None
