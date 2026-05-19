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
import logging
import sys
from pathlib import Path
from typing import Optional

from ompa import Ompa, __version__
from ompa.config import make_ompa

logger = logging.getLogger(__name__)
from ompa.mcp_tools import TOOLS


def _make_ompa(arguments: dict, enable_semantic: bool = False) -> Ompa:
    return make_ompa(
        vault_path=arguments.get("vault_path", "."),
        shared_vault_path=arguments.get("shared_vault_path"),
        personal_vault_path=arguments.get("personal_vault_path"),
        enable_semantic=enable_semantic,
    )


# ---------------------------------------------------------------------------
# Tool Implementations
# ---------------------------------------------------------------------------


def ao_session_start(vault_path: str = ".") -> dict:
    """
    Start a session. Loads vault context: file listing, North Star,
    active work, palace wings, KG stats. ~2K tokens.
    """
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    result = ao.session_start()
    return {
        "success": result.success,
        "output": result.output,
        "tokens": result.tokens_hint,
    }


def ao_classify(message: str, vault_path: str = ".") -> dict:
    """Classify a user message into one of 15 types."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    c = ao.classify(message)
    return {
        "message_type": c.message_type.value,
        "confidence": c.confidence,
        "action": c.suggested_action,
        "routing_hints": c.routing_hints,
    }


def ao_search(query: str, vault_path: str = ".", limit: int = 5) -> dict:
    """Search the vault with hybrid semantic + keyword search."""
    ao = Ompa(vault_path=vault_path, enable_semantic=True)
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


def ao_kg_query(entity: str, vault_path: str = ".", as_of: Optional[str] = None) -> dict:
    """Query the knowledge graph for an entity."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
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
    valid_from: Optional[str] = None,
    source: Optional[str] = None,
    vault_path: str = ".",
) -> dict:
    """
    Add a fact to the knowledge graph.
    """
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
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
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    return ao.kg.stats()


def ao_palace_wings(vault_path: str = ".") -> dict:
    """List all palace wings."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    return {"wings": ao.palace.list_wings()}


def ao_palace_rooms(wing: str, vault_path: str = ".") -> dict:
    """List rooms in a wing."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    rooms = ao.palace.list_rooms(wing)
    return {"wing": wing, "rooms": rooms}


def ao_palace_tunnel(
    wing_a: str, wing_b: str, room: str, vault_path: str = "."
) -> dict:
    """Create a tunnel between two wings."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    ao.palace.create_tunnel(wing_a, wing_b, room)
    return {"success": True, "tunnel": f"{wing_a} <-> {wing_b} via {room}"}


def ao_validate(file_path: str, vault_path: str = ".") -> dict:
    """Validate a markdown file."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    return ao.validate_write(file_path)


def ao_wrap_up(vault_path: str = ".") -> dict:
    """Run session wrap-up."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    result = ao.stop()
    return {"success": result.success, "output": result.output}


def ao_status(vault_path: str = ".") -> dict:
    """Get full status (vault + palace + KG)."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    return {
        "vault": ao.get_stats(),
        "palace": ao.palace.stats(),
        "kg": ao.kg.stats(),
    }


def ao_orphans(vault_path: str = ".") -> dict:
    """Find orphan notes."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    orphans = ao.find_orphans()
    return {
        "orphan_count": len(orphans),
        "orphans": [str(o.path) for o in orphans[:20]],
    }


def ao_kg_populate(vault_path: str = ".") -> dict:
    """
    Populate the knowledge graph from all vault notes.
    Extracts wikilinks, tags, folder structure, and dates into triples.
    """
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    count = ao.kg_populate()
    stats = ao.kg.stats()
    return {
        "success": True,
        "triples_added": count,
        "total_entities": stats["entity_count"],
        "total_facts": stats["triple_count"],
    }


def ao_sync(vault_path: str = ".") -> dict:
    """
    Full sync: rebuild KG, palace, and search index from vault.
    """
    ao = Ompa(vault_path=vault_path, enable_semantic=True)
    result = ao.sync()
    return {"success": True, **result}


def ao_write(arguments: dict) -> dict:
    """Write content to the appropriate vault (auto-classifies in dual mode)."""
    ao = _make_ompa(arguments, enable_semantic=False)
    content = str(arguments.get("content", ""))
    tags_raw = arguments.get("tags", "")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
    file_path: Optional[str] = arguments.get("file_path")
    vault: Optional[str] = arguments.get("vault")
    result = ao.write(
        content,
        file_path=file_path,
        tags=tags,
        vault=vault,
    )
    return result


def ao_export(arguments: dict) -> dict:
    """Export a note from personal vault to shared vault."""
    ao = _make_ompa(arguments, enable_semantic=False)
    return ao.export_to_shared(
        note_path=arguments["note_path"],
        confirm=arguments.get("confirm", True),
        sanitize=arguments.get("sanitize", True),
    )


def ao_import(arguments: dict) -> dict:
    """Import a note from shared vault to personal vault."""
    ao = _make_ompa(arguments, enable_semantic=False)
    return ao.import_to_personal(
        note_path=arguments["note_path"],
        link_back=arguments.get("link_back", True),
    )


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
# Tool dispatch — routes a validated call to its handler
# ---------------------------------------------------------------------------


def _dispatch_tool(name: str, arguments: dict, vault_path: str) -> dict:
    """
    Route a validated tool call to its implementation.

    Tools that take only vault_path are called directly.
    Tools that need extra arguments extract them from the arguments dict.
    Dual-vault tools (ao_write, ao_export, ao_import) receive the full
    arguments dict so they can read shared_vault_path / personal_vault_path.
    """
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
# MCP Protocol helpers
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
    """Validate and dispatch a tool call."""
    if name not in TOOLS:
        return {"error": f"Unknown tool: {name}"}

    try:
        vault_path = str(arguments.get("vault_path", "."))
        # Block obvious traversal attempts
        if ".." in vault_path or vault_path in ("/", "C:\\", "C:/"):
            return {"error": "Invalid vault_path"}
        # Cap limit parameters
        if "limit" in arguments:
            arguments = {**arguments, "limit": min(int(arguments["limit"]), 100)}

        return _dispatch_tool(name, arguments, vault_path)

    except KeyError as e:
        return {"error": f"Missing required argument: {e}"}
    except Exception as e:
        return {"error": type(e).__name__}


# ---------------------------------------------------------------------------
# MCP Protocol — JSON-RPC over stdin/stdout
# ---------------------------------------------------------------------------


def main():
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
            logger.exception("MCP protocol error: %s", e)
            error_response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32603, "message": f"{type(e).__name__}: {e}"},
            }
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()
            request = None  # Reset for next iteration


if __name__ == "__main__":
    main()
