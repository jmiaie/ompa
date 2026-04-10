"""
OMPA MCP Server
Provides 14 tools via the Model Context Protocol.
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
from typing import Any

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
    return {
        "success": result.success,
        "output": result.output,
        "tokens_hint": result.tokens_hint,
        "error": result.error,
    }


def ao_classify(message: str, vault_path: str = ".") -> dict:
    """
    Classify a user message into one of 15 types and get routing hints.
    ~100 tokens. Run on every user message.
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    c = ao.classify(message)
    return {
        "message_type": c.message_type.value,
        "confidence": c.confidence,
        "suggested_action": c.suggested_action,
        "routing_hints": c.routing_hints,
        "suggested_folder": c.suggested_folder,
    }


def ao_search(query: str, vault_path: str = ".", limit: int = 5) -> dict:
    """
    Semantic search across the vault. Local embeddings (zero API cost).
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=True)
    results = ao.search(query, limit=limit)
    return {
        "results": [
            {
                "path": r.path,
                "excerpt": r.content_excerpt,
                "score": r.score,
                "match_type": r.match_type,
            }
            for r in results
        ],
        "count": len(results),
    }


def ao_kg_query(entity: str, as_of: str = None, vault_path: str = ".") -> dict:
    """
    Query the knowledge graph for an entity.
    Pass as_of in YYYY-MM-DD format for historical query.
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    triples = ao.kg.query_entity(entity, as_of=as_of)
    return {
        "entity": entity,
        "as_of": as_of,
        "triples": [
            {
                "subject": t.subject,
                "predicate": t.predicate,
                "object": t.object,
                "valid_from": t.valid_from,
                "valid_to": t.valid_to,
                "source": t.source_file,
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
        subject, predicate, object_,
        valid_from=valid_from,
        source=source,
    )
    return {"success": True, "added": f"{subject} --{predicate}--> {object_}"}


def ao_kg_stats(vault_path: str = ".") -> dict:
    """
    Get knowledge graph statistics.
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    return ao.kg.stats()


def ao_palace_wings(vault_path: str = ".") -> dict:
    """
    List all palace wings (people and projects).
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    wings = ao.palace.list_wings()
    return {"wings": wings, "count": len(wings)}


def ao_palace_rooms(wing: str, vault_path: str = ".") -> dict:
    """
    List all rooms in a wing.
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    rooms = ao.palace.list_rooms(wing)
    return {"wing": wing, "rooms": rooms, "count": len(rooms)}


def ao_palace_tunnel(wing_a: str, wing_b: str, vault_path: str = ".") -> dict:
    """
    Find tunnels (cross-wing connections) between two wings.
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    tunnels = ao.palace.find_tunnels(wing_a, wing_b)
    return {
        "wing_a": wing_a,
        "wing_b": wing_b,
        "tunnels": tunnels,
        "count": len(tunnels),
    }


def ao_validate(file_path: str, vault_path: str = ".") -> dict:
    """
    Validate a markdown file for frontmatter and wikilinks.
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    result = ao.validate_write(file_path)
    return {
        "file_path": file_path,
        "valid": result["valid"],
        "warnings": result.get("warnings", []),
    }


def ao_wrap_up(vault_path: str = ".") -> dict:
    """
    Run session wrap-up: orphan check, North Star check, KG stats.
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    result = ao.stop()
    return {
        "success": result.success,
        "output": result.output,
        "error": result.error,
    }


def ao_status(vault_path: str = ".") -> dict:
    """
    Get full vault + palace + KG status overview.
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    vault_stats = ao.get_stats()
    palace_wings = ao.palace.list_wings()
    kg_stats = ao.kg.stats()
    return {
        "vault": vault_stats,
        "palace_wings": palace_wings,
        "palace_wing_count": len(palace_wings),
        "kg": kg_stats,
    }


def ao_orphans(vault_path: str = ".") -> dict:
    """
    Find all orphan notes (no wikilinks).
    """
    AO = _load_core()
    ao = AO(vault_path=vault_path, enable_semantic=False)
    orphans = ao.find_orphans()
    return {
        "orphans": [str(o.path) for o in orphans],
        "count": len(orphans),
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
# MCP Protocol Handler
# ---------------------------------------------------------------------------

TOOLS = {
    "ao_session_start": {
        "description": "Start a session. Loads vault context (~2K tokens): file listing, North Star goals, active work, palace wings, KG stats. Call at session start.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {
                    "type": "string",
                    "description": "Path to the vault directory. Defaults to current directory.",
                    "default": ".",
                }
            },
        },
    },
    "ao_classify": {
        "description": "Classify a user message into 15 types (DECISION, INCIDENT, WIN, QUESTION, etc.) and get routing hints. ~100 tokens. Call on every user message.",
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "The user message to classify.",
                },
                "vault_path": {
                    "type": "string",
                    "description": "Path to the vault directory.",
                    "default": ".",
                },
            },
            "required": ["message"],
        },
    },
    "ao_search": {
        "description": "Semantic search across the vault using local embeddings (zero API cost). Returns ranked results with excerpts.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query.",
                },
                "vault_path": {
                    "type": "string",
                    "description": "Path to the vault directory.",
                    "default": ".",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum results to return.",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
    "ao_kg_query": {
        "description": "Query the temporal knowledge graph for an entity. Returns all facts about the entity. Use as_of for historical queries.",
        "input_schema": {
            "type": "object",
            "properties": {
                "entity": {
                    "type": "string",
                    "description": "Entity name to query.",
                },
                "as_of": {
                    "type": "string",
                    "description": "Historical date filter (YYYY-MM-DD).",
                },
                "vault_path": {
                    "type": "string",
                    "description": "Path to the vault directory.",
                    "default": ".",
                },
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
                "valid_from": {"type": "string", "description": "Start date (YYYY-MM-DD)."},
                "source": {"type": "string", "description": "Source file or drawer reference."},
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
        "description": "List all palace wings (people and projects).",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_palace_rooms": {
        "description": "List all rooms in a palace wing.",
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
        "description": "Find tunnel connections between two wings.",
        "input_schema": {
            "type": "object",
            "properties": {
                "wing_a": {"type": "string", "description": "First wing."},
                "wing_b": {"type": "string", "description": "Second wing."},
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
                "file_path": {"type": "string", "description": "Path to the file to validate."},
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
        "description": "Get full vault + palace + KG status overview.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_orphans": {
        "description": "Find all orphan notes (notes with no wikilinks).",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
            },
        },
    },
    "ao_init": {
        "description": "Initialize a new vault + palace structure.",
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
        tools.append({
            "name": name,
            "description": spec["description"],
            "inputSchema": spec["input_schema"],
        })
    return {"tools": tools}


def handle_call_tool(name: str, arguments: dict) -> dict:
    """Handle tool call request."""
    if name not in TOOLS:
        return {"error": f"Unknown tool: {name}"}

    try:
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
                as_of=arguments.get("as_of"),
                vault_path=vault_path,
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
            result = {"error": f"Tool {name} not yet implemented"}

        return {"result": result}

    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------------------
# MCP Protocol — JSON-RPC over stdin/stdout
# ---------------------------------------------------------------------------

def _read_message() -> dict | None:
    """Read a JSON-RPC message from stdin using content-length framing."""
    # Try content-length header first (standard MCP/LSP framing)
    header_line = sys.stdin.readline()
    if not header_line:
        return None

    header_line = header_line.strip()

    # Support content-length framed messages
    if header_line.lower().startswith("content-length:"):
        content_length = int(header_line.split(":", 1)[1].strip())
        # Read blank separator line
        sys.stdin.readline()
        body = sys.stdin.read(content_length)
        return json.loads(body)

    # Fallback: bare JSON line (for simple testing / piped input)
    if header_line.startswith("{"):
        return json.loads(header_line)

    return None


def _write_message(response: dict) -> None:
    """Write a JSON-RPC response with content-length framing."""
    body = json.dumps(response)
    sys.stdout.write(f"Content-Length: {len(body)}\r\n\r\n{body}")
    sys.stdout.flush()


def _handle_request(request: dict) -> dict | None:
    """Process a single JSON-RPC request and return a response (or None for notifications)."""
    method = request.get("method", "")
    request_id = request.get("id")

    if method == "initialize":
        return {
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

    elif method == "notifications/initialized":
        return None  # Notification, no response needed

    elif method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": handle_list_tools(),
        }

    elif method == "tools/call":
        name = request["params"]["name"]
        arguments = request["params"].get("arguments", {})
        result = handle_call_tool(name, arguments)
        return {
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

    elif method in ("shutdown", "exit"):
        return None  # Signal to exit

    else:
        if request_id is not None:
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            }
        return None


def main():
    """
    MCP server main loop.
    Reads JSON-RPC requests from stdin, writes responses to stdout.
    Supports both content-length framing (standard MCP) and bare JSON lines.
    """
    while True:
        try:
            request = _read_message()
            if request is None:
                break

            if request.get("method") in ("shutdown", "exit"):
                break

            response = _handle_request(request)
            if response is not None:
                _write_message(response)

        except Exception as e:
            error_response = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(e)},
            }
            _write_message(error_response)


if __name__ == "__main__":
    main()
