"""
OMPA MCP Server
Provides 15+ tools via the Model Context Protocol.
Works with Claude Desktop, Cursor, Windsurf, and any MCP-compatible client.

Usage:
    # Claude Desktop
    claude mcp add ompa -- python -m ompa.mcp_server

    # Then in any Claude session, use the tools:
    # - ao_session_start: Start a session, load context
    # - ao_classify: Classify a message
    # - ao_search: Search the vault
    # - ao_kg_query: Query the knowledge graph
    # - ao_palace_wings: List palace wings
    # - etc.

Tool implementations live in ompa/mcp_tools.py.
This module owns the JSON-Schema descriptors, tool dispatch, and JSON-RPC loop.
"""

import json
import sys

from ompa import __version__
from ompa.mcp_tools import (
    ao_classify,
    ao_export,
    ao_import,
    ao_init,
    ao_kg_add,
    ao_kg_populate,
    ao_kg_query,
    ao_kg_stats,
    ao_orphans,
    ao_palace_rooms,
    ao_palace_tunnel,
    ao_palace_wings,
    ao_search,
    ao_session_start,
    ao_status,
    ao_sync,
    ao_validate,
    ao_wrap_up,
    ao_write,
)


# ---------------------------------------------------------------------------
# Tool Schema Definitions
# ---------------------------------------------------------------------------

TOOLS = {
    "ao_session_start": {
        "description": "Start a session. Loads vault context (~2K tokens): file listing, North Star goals, active work, palace wings, KG stats.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_classify": {
        "description": "Classify a user message into one of 15 types (DECISION, INCIDENT, WIN, etc.) with routing hints.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The message to classify.",
                },
                "vault_path": {"type": "string", "default": "."},
            },
            "required": ["message"],
        },
    },
    "ao_search": {
        "description": "Search the vault with hybrid semantic + keyword search. Returns scored results with excerpts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query."},
                "vault_path": {"type": "string", "default": "."},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["query"],
        },
    },
    "ao_kg_query": {
        "description": "Query the knowledge graph for all facts about an entity. Supports temporal queries with as_of date.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {"type": "string", "description": "Entity name to query."},
                "as_of": {
                    "type": "string",
                    "description": "Historical date (YYYY-MM-DD).",
                },
                "vault_path": {"type": "string", "default": "."},
            },
            "required": ["entity"],
        },
    },
    "ao_kg_add": {
        "description": "Add a fact (triple) to the knowledge graph. Subject-predicate-object with optional validity window.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "Subject entity."},
                "predicate": {"type": "string", "description": "Relationship verb."},
                "object": {"type": "string", "description": "Object entity."},
                "valid_from": {
                    "type": "string",
                    "description": "Start date (YYYY-MM-DD).",
                },
                "source": {
                    "type": "string",
                    "description": "Source file or drawer reference.",
                },
                "vault_path": {"type": "string", "default": "."},
            },
            "required": ["subject", "predicate", "object"],
        },
    },
    "ao_kg_stats": {
        "description": "Get knowledge graph statistics.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_palace_wings": {
        "description": "List all palace wings (top-level memory categories).",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_palace_rooms": {
        "description": "List rooms in a palace wing.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing": {"type": "string", "description": "Wing name."},
                "vault_path": {"type": "string", "default": "."},
            },
            "required": ["wing"],
        },
    },
    "ao_palace_tunnel": {
        "description": "Create a tunnel (cross-wing connection) between two wings via a shared room.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing_a": {"type": "string"},
                "wing_b": {"type": "string"},
                "room": {"type": "string"},
                "vault_path": {"type": "string", "default": "."},
            },
            "required": ["wing_a", "wing_b"],
        },
    },
    "ao_validate": {
        "description": "Validate a markdown file for frontmatter (date, description, tags) and wikilinks.",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Path to the file to validate.",
                },
                "vault_path": {"type": "string", "default": "."},
            },
            "required": ["file_path"],
        },
    },
    "ao_wrap_up": {
        "description": "Run session wrap-up: orphan check, North Star check, KG stats.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_status": {
        "description": "Get full status: vault stats, palace stats, KG stats.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_orphans": {
        "description": "Find orphan notes (notes with no wikilinks pointing to them).",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_kg_populate": {
        "description": "Populate the knowledge graph from all vault notes. Extracts wikilinks, tags, folder structure, and dates into triples.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_sync": {
        "description": "Full sync: rebuild knowledge graph, palace metadata, and search index from vault notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_write": {
        "description": "Write content to the appropriate vault. Auto-classifies in dual-vault mode (shared vs personal).",
        "input_schema": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "Note content."},
                "vault": {
                    "type": "string",
                    "description": "Target vault: shared or personal.",
                },
                "tags": {
                    "type": "string",
                    "description": "Comma-separated tags.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Target file path.",
                },
                "vault_path": {"type": "string", "default": "."},
                "shared_vault_path": {
                    "type": "string",
                    "description": "Shared vault path (for dual mode).",
                },
                "personal_vault_path": {
                    "type": "string",
                    "description": "Personal vault path (for dual mode).",
                },
            },
            "required": ["content"],
        },
    },
    "ao_export": {
        "description": "Export a note from personal vault to shared vault. Sanitizes credentials by default.",
        "input_schema": {
            "type": "object",
            "properties": {
                "note_path": {
                    "type": "string",
                    "description": "Path relative to personal vault.",
                },
                "confirm": {"type": "boolean", "default": True},
                "sanitize": {"type": "boolean", "default": True},
                "shared_vault_path": {"type": "string"},
                "personal_vault_path": {"type": "string"},
            },
            "required": [
                "note_path",
                "shared_vault_path",
                "personal_vault_path",
            ],
        },
    },
    "ao_import": {
        "description": "Import a note from shared vault to personal vault.",
        "input_schema": {
            "type": "object",
            "properties": {
                "note_path": {
                    "type": "string",
                    "description": "Path relative to shared vault.",
                },
                "link_back": {"type": "boolean", "default": True},
                "shared_vault_path": {"type": "string"},
                "personal_vault_path": {"type": "string"},
            },
            "required": [
                "note_path",
                "shared_vault_path",
                "personal_vault_path",
            ],
        },
    },
    "ao_init": {
        "description": "Initialize a new vault + palace structure with folders and brain notes.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------


def handle_list_tools() -> dict:
    """Handle tool list request."""
    tools = [
        {
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["input_schema"],
        }
        for name, spec in TOOLS.items()
    ]
    return {"tools": tools}


def handle_call_tool(name: str, arguments: dict) -> dict:
    """Validate, dispatch, and call a named tool."""
    if name not in TOOLS:
        return {"error": f"Unknown tool: {name}"}

    try:
        # Validate vault_path to block path-traversal attempts
        vault_path = str(arguments.get("vault_path", "."))
        if ".." in vault_path or vault_path in ("/", "C:\\", "C:/"):
            return {"error": "Invalid vault_path"}
        if "limit" in arguments:
            arguments = {**arguments, "limit": min(int(arguments["limit"]), 100)}

        return _dispatch(name, vault_path, arguments)
    except KeyError as e:
        return {"error": f"Missing required argument: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def _dispatch(name: str, vault_path: str, arguments: dict) -> dict:
    """Map tool name to implementation function."""
    if name == "ao_session_start":
        return ao_session_start(vault_path)
    if name == "ao_classify":
        return ao_classify(message=arguments["message"], vault_path=vault_path)
    if name == "ao_search":
        return ao_search(
            query=arguments["query"],
            vault_path=vault_path,
            limit=arguments.get("limit", 5),
        )
    if name == "ao_kg_query":
        return ao_kg_query(
            entity=arguments["entity"],
            vault_path=vault_path,
            as_of=arguments.get("as_of"),
        )
    if name == "ao_kg_add":
        return ao_kg_add(
            subject=arguments["subject"],
            predicate=arguments["predicate"],
            object_=arguments["object"],
            valid_from=arguments.get("valid_from"),
            source=arguments.get("source"),
            vault_path=vault_path,
        )
    if name == "ao_kg_stats":
        return ao_kg_stats(vault_path)
    if name == "ao_palace_wings":
        return ao_palace_wings(vault_path)
    if name == "ao_palace_rooms":
        return ao_palace_rooms(wing=arguments["wing"], vault_path=vault_path)
    if name == "ao_palace_tunnel":
        return ao_palace_tunnel(
            wing_a=arguments["wing_a"],
            wing_b=arguments["wing_b"],
            room=arguments.get("room", "shared"),
            vault_path=vault_path,
        )
    if name == "ao_validate":
        return ao_validate(file_path=arguments["file_path"], vault_path=vault_path)
    if name == "ao_wrap_up":
        return ao_wrap_up(vault_path)
    if name == "ao_status":
        return ao_status(vault_path)
    if name == "ao_orphans":
        return ao_orphans(vault_path)
    if name == "ao_kg_populate":
        return ao_kg_populate(vault_path)
    if name == "ao_sync":
        return ao_sync(vault_path)
    if name == "ao_write":
        return ao_write(arguments)
    if name == "ao_export":
        return ao_export(arguments)
    if name == "ao_import":
        return ao_import(arguments)
    if name == "ao_init":
        return ao_init(vault_path)
    return {"error": f"Unhandled tool: {name}"}


# ---------------------------------------------------------------------------
# MCP Protocol -- JSON-RPC over stdin/stdout
# ---------------------------------------------------------------------------


def _make_response(request_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _make_error_response(request_id, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": -32603, "message": message},
    }


def main():
    """
    MCP server main loop.
    Reads JSON-RPC requests from stdin, writes responses to stdout.
    """
    request = None  # must be initialized so the except block can read request.id safely
    while True:
        try:
            line = sys.stdin.readline()
            if not line:
                break

            request = json.loads(line.strip())
            method = request.get("method", "")
            request_id = request.get("id")

            if method == "initialize":
                response = _make_response(
                    request_id,
                    {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {"tools": {}},
                        "serverInfo": {"name": "ompa", "version": __version__},
                    },
                )
            elif method == "tools/list":
                response = _make_response(request_id, handle_list_tools())
            elif method == "tools/call":
                name = request["params"]["name"]
                arguments = request["params"].get("arguments", {})
                result = handle_call_tool(name, arguments)
                response = _make_response(
                    request_id,
                    {
                        "content": [
                            {"type": "text", "text": json.dumps(result, indent=2)}
                        ]
                    },
                )
            elif method in ("notifications/initialized",):
                continue  # MCP lifecycle notification, no response needed
            elif method in ("shutdown", "exit"):
                break
            else:
                request = None
                continue

            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()

        except Exception as e:
            req_id = None
            if request is not None and isinstance(request, dict):
                req_id = request.get("id")
            error_response = _make_error_response(req_id, f"{type(e).__name__}: {e}")
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()
            request = None


if __name__ == "__main__":
    main()
