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
"""

import json
import sys
from pathlib import Path

__version__ = "0.2.0"


# ---------------------------------------------------------------------------
# Tool Implementations
# ---------------------------------------------------------------------------


def _load_core():
    """Lazy-load the core module."""
    from ompa import Ompa

    return Ompa


def ao_session_start(vault_path: str = ".") -> dict:
    """
    Start a session. Loads vault context: file listing, North Star,
    active work, palace wings, KG stats. ~2K tokens.
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    result = ao.session_start()
    return {"success": result.success, "output": result.output, "tokens": result.tokens_hint}


def ao_classify(message: str, vault_path: str = ".") -> dict:
    """Classify a user message into one of 15 types."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    c = ao.classify(message)
    return {
        "message_type": c.message_type.value,
        "confidence": c.confidence,
        "action": c.suggested_action,
        "routing_hints": c.routing_hints,
    }


def ao_search(query: str, vault_path: str = ".", limit: int = 5) -> dict:
    """Search the vault with hybrid semantic + keyword search."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=True)
    results = ao.search(query, limit=limit)
    return {
        "results": [
            {
                "path": r.path,
                "excerpt": r.content_excerpt,
                "score": r.score,
                "type": r.match_type,
            }
            for r in results
        ]
    }


def ao_kg_query(entity: str, vault_path: str = ".", as_of: str = None) -> dict:
    """Query the knowledge graph for an entity."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    triples = ao.kg.query_entity(entity, as_of=as_of)
    return {
        "entity": entity,
        "facts": [
            {
                "subject": t.subject,
                "predicate": t.predicate,
                "object": t.object,
                "valid_from": t.valid_from,
                "valid_to": t.valid_to,
            }
            for t in triples
        ],
    }


def ao_kg_add(
    subject: str,
    predicate: str,
    object_: str,
    valid_from: str = None,
    source: str = None,
    vault_path: str = ".",
) -> dict:
    """
    Add a fact to the knowledge graph.
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    ao.kg.add_triple(
        subject,
        predicate,
        object_,
        valid_from=valid_from,
        source=source,
    )
    return {"success": True, "added": f"{subject} --{predicate}--> {object_}"}


def ao_kg_stats(vault_path: str = ".") -> dict:
    """Get knowledge graph statistics."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    return ao.kg.stats()


def ao_palace_wings(vault_path: str = ".") -> dict:
    """List all palace wings."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    return {"wings": ao.palace.list_wings()}


def ao_palace_rooms(wing: str, vault_path: str = ".") -> dict:
    """List rooms in a wing."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    rooms = ao.palace.list_rooms(wing)
    return {"wing": wing, "rooms": rooms}


def ao_palace_tunnel(wing_a: str, wing_b: str, room: str, vault_path: str = ".") -> dict:
    """Create a tunnel between two wings."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    ao.palace.create_tunnel(wing_a, wing_b, room)
    return {"success": True, "tunnel": f"{wing_a} <-> {wing_b} via {room}"}


def ao_validate(file_path: str, vault_path: str = ".") -> dict:
    """Validate a markdown file."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    return ao.validate_write(file_path)


def ao_wrap_up(vault_path: str = ".") -> dict:
    """Run session wrap-up."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    result = ao.stop()
    return {"success": result.success, "output": result.output}


def ao_status(vault_path: str = ".") -> dict:
    """Get full status (vault + palace + KG)."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    return {
        "vault": ao.get_stats(),
        "palace": ao.palace.stats(),
        "kg": ao.kg.stats(),
    }


def ao_orphans(vault_path: str = ".") -> dict:
    """Find orphan notes."""
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    orphans = ao.find_orphans()
    return {
        "orphan_count": len(orphans),
        "orphans": [str(o.path) for o in orphans[:20]],
    }


def ao_init(vault_path: str = ".") -> dict:
    """
    Initialize a new vault + palace structure.
    Creates all folders and essential brain notes.
    """
    from ompa import Vault

    vault = Vault(vault_path)
    stats = vault.get_stats()
    return {
        "success": True,
        "initialized": str(Path(vault_path).absolute()),
        "notes": stats["total_notes"],
        "brain_notes": stats["brain_notes"],
    }


# ---------------------------------------------------------------------------
# Tool Definitions
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
                "message": {"type": "string", "description": "The message to classify."},
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
                "as_of": {"type": "string", "description": "Historical date (YYYY-MM-DD)."},
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


def handle_list_tools():
    """Handle tool list request."""
    tools = []
    for name, spec in TOOLS.items():
        tools.append(
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": spec["input_schema"],
            }
        )
    return {"tools": tools}


def handle_call_tool(name: str, arguments: dict) -> dict:
    """Handle tool call request."""
    if name not in TOOLS:
        return {"error": f"Unknown tool: {name}"}

    try:
        # Extract vault_path without mutating the original dict
        vault_path = arguments.get("vault_path", ".")

        if name == "ao_session_start":
            result = ao_session_start(vault_path)
        elif name == "ao_classify":
            result = ao_classify(
                message=arguments["message"],
                vault_path=vault_path,
            )
        elif name == "ao_search":
            result = ao_search(
                query=arguments["query"],
                vault_path=vault_path,
                limit=arguments.get("limit", 5),
            )
        elif name == "ao_kg_query":
            result = ao_kg_query(
                entity=arguments["entity"],
                vault_path=vault_path,
                as_of=arguments.get("as_of"),
            )
        elif name == "ao_kg_add":
            result = ao_kg_add(
                subject=arguments["subject"],
                predicate=arguments["predicate"],
                object_=arguments["object"],
                valid_from=arguments.get("valid_from"),
                source=arguments.get("source"),
                vault_path=vault_path,
            )
        elif name == "ao_kg_stats":
            result = ao_kg_stats(vault_path)
        elif name == "ao_palace_wings":
            result = ao_palace_wings(vault_path)
        elif name == "ao_palace_rooms":
            result = ao_palace_rooms(
                wing=arguments["wing"],
                vault_path=vault_path,
            )
        elif name == "ao_palace_tunnel":
            result = ao_palace_tunnel(
                wing_a=arguments["wing_a"],
                wing_b=arguments["wing_b"],
                room=arguments.get("room", "shared"),
                vault_path=vault_path,
            )
        elif name == "ao_validate":
            result = ao_validate(
                file_path=arguments["file_path"],
                vault_path=vault_path,
            )
        elif name == "ao_wrap_up":
            result = ao_wrap_up(vault_path)
        elif name == "ao_status":
            result = ao_status(vault_path)
        elif name == "ao_orphans":
            result = ao_orphans(vault_path)
        elif name == "ao_init":
            result = ao_init(vault_path)
        else:
            result = {"error": f"Unhandled tool: {name}"}

        return result
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# MCP Protocol — JSON-RPC over stdin/stdout
# ---------------------------------------------------------------------------


def main():
    """
    MCP server main loop.
    Reads JSON-RPC requests from stdin, writes responses to stdout.
    """
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
                print(json.dumps(response), flush=True)

            elif method == "tools/list":
                response = {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": handle_list_tools(),
                }
                print(json.dumps(response), flush=True)

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
                print(json.dumps(response), flush=True)

            else:
                # Notified of shutdown
                if method in ("shutdown", "exit"):
                    break

        except Exception as e:
            error_response = {
                "jsonrpc": "2.0",
                "id": request.get("id") if request else None,
                "error": {"code": -32603, "message": str(e)},
            }
            print(json.dumps(error_response), flush=True)


if __name__ == "__main__":
    main()
