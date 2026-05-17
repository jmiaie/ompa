"""Tests for ompa.hooks — Lifecycle hooks."""

import tempfile


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
