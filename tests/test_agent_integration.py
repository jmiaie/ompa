"""Tests for ompa.agent_integration — Session cold-start safety."""

import tempfile


class TestAgentIntegration:
    """Tests for agent_integration.Session cold-start safety (Issue #6)."""

    def test_construction_does_not_initialize_memory(self):
        """Session() must not eagerly construct Ompa — memory stays None."""
        from ompa.agent_integration import Session

        with tempfile.TemporaryDirectory() as tmpdir:
            s = Session(vault_path=tmpdir, enable_semantic=False)
            assert s._memory is None

    def test_memory_property_lazy_init(self):
        """Accessing .memory for the first time creates the Ompa instance."""
        from ompa import Ompa
        from ompa.agent_integration import Session

        with tempfile.TemporaryDirectory() as tmpdir:
            s = Session(vault_path=tmpdir, enable_semantic=False)
            assert s._memory is None
            mem = s.memory
            assert isinstance(mem, Ompa)
            assert s._memory is mem

    def test_memory_property_idempotent(self):
        """Accessing .memory twice returns the same object."""
        from ompa.agent_integration import Session

        with tempfile.TemporaryDirectory() as tmpdir:
            s = Session(vault_path=tmpdir, enable_semantic=False)
            assert s.memory is s.memory

    def test_session_start_cold_start_no_prior_init(self):
        """session_start() works without calling session_init() first (Issue #6)."""
        from ompa.agent_integration import Session

        with tempfile.TemporaryDirectory() as tmpdir:
            s = Session(vault_path=tmpdir, enable_semantic=False)
            result = s.session_start()
            assert result.success

    def test_session_init_then_session_start(self):
        """session_init() followed by session_start() works correctly."""
        from ompa.agent_integration import Session

        with tempfile.TemporaryDirectory() as tmpdir:
            s = Session(vault_path=tmpdir, enable_semantic=False)
            s.session_init()
            assert s._memory is not None
            result = s.session_start()
            assert result.success

    def test_hasattr_memory_always_true(self):
        """hasattr(session, 'memory') is True even before first access."""
        from ompa.agent_integration import Session

        with tempfile.TemporaryDirectory() as tmpdir:
            s = Session(vault_path=tmpdir, enable_semantic=False)
            assert hasattr(s, "memory")

    def test_context_manager(self):
        """Session works as a context manager: start on enter, stop on exit."""
        from ompa.agent_integration import Session

        with tempfile.TemporaryDirectory() as tmpdir, Session(vault_path=tmpdir, enable_semantic=False) as s:
            assert s._memory is not None

    def test_make_ompa_is_patchable(self, monkeypatch):
        """_make_ompa can be patched to inject a fake Ompa for unit testing."""
        import ompa.agent_integration as ai
        from ompa.hooks import HookResult

        class FakeOmpa:
            def session_start(self):
                return HookResult(hook_name="session_start", success=True, output="ok")

            def stop(self):
                return HookResult(hook_name="stop", success=True, output="done")

        monkeypatch.setattr(ai, "_make_ompa", lambda *a, **kw: FakeOmpa())

        with tempfile.TemporaryDirectory() as tmpdir:
            s = ai.Session(vault_path=tmpdir, enable_semantic=False)
            result = s.session_start()
            assert result.success
            assert result.output == "ok"
