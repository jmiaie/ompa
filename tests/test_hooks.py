"""Tests for hooks."""

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


