"""
OMPA MCP Server — JSON-RPC protocol loop over stdin/stdout.

Tool implementations and schema definitions live in mcp_tools.py.
This module handles only the MCP wire protocol.

Usage:
    # Claude Desktop
    claude mcp add ompa -- python -m ompa.mcp_server

    # Available tools: ao_session_start, ao_classify, ao_search, ao_kg_query,
    # ao_kg_add, ao_kg_stats, ao_palace_wings, ao_palace_rooms,
    # ao_palace_tunnel, ao_validate, ao_wrap_up, ao_status, ao_orphans,
    # ao_kg_populate, ao_sync, ao_write, ao_export, ao_import, ao_init
"""

import json
import sys

from ompa import __version__
from ompa.mcp_tools import handle_call_tool, handle_list_tools


def main() -> None:
    """
    MCP server main loop.
    Reads JSON-RPC requests from stdin, writes responses to stdout.
    """
    request = None  # Initialize to prevent NameError on malformed JSON
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            request = json.loads(line.strip())
            method = request.get("method", "")
            request_id = request.get("id")

            if method == "initialize":
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {
                            "name": "ompa",
                            "version": __version__,
                        },
                    },
                }
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

            elif method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": handle_list_tools(),
                }
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

            elif method == "tools/call":
                name = request["params"]["name"]
                arguments = request["params"].get("arguments", {})
                result = handle_call_tool(name, arguments)
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "content": [
                            {
                                "type": "text",
                                "text": json.dumps(result, indent=2),
                            }
                        ]
                    },
                }
                sys.stdout.write(json.dumps(response) + "\n")
                sys.stdout.flush()

            elif method in ("notifications/initialized",):
                pass  # MCP lifecycle notification, no response needed

            else:
                if method in ("shutdown", "exit"):
                    break

        except Exception as e:
            req_id = None
            if request is not None:
                req_id = request.get("id") if isinstance(request, dict) else None
            error_response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": type(e).__name__},
            }
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()
            request = None  # Reset for next iteration


if __name__ == "__main__":
    main()
