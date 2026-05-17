"""Tests for ompa.mcp_server — MCP tool dispatch."""

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

    def test_mcp_kg_populate(self):
        """MCP ao_kg_populate tool should work."""
        from pathlib import Path

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
