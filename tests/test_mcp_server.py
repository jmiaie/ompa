"""Tests for mcp_server."""

import tempfile


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

        result = handle_call_tool("ao_status", {"vault_path": "../../../etc"})
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
            assert (
                "Invalid" in result["error"]
            ), f"Expected 'Invalid' in error for {bad!r}: {result['error']}"

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
        from ompa.mcp_server import _clear_ompa_cache, _get_ompa

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
        from ompa.mcp_server import _clear_ompa_cache, _get_ompa

        with (
            tempfile.TemporaryDirectory() as shared,
            tempfile.TemporaryDirectory() as personal,
        ):
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


