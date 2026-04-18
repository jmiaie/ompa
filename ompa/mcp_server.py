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

# Re-export the package version so MCP clients can probe it via the server
# module. Sourced from package metadata so it can never drift from pyproject.
from . import __version__

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path validation
# ---------------------------------------------------------------------------

# Resolved paths that are never valid as a vault. Any vault_path that equals
# one of these, or is inside one of them, is rejected. Compared case-
# insensitively with forward-slash normalization to handle Windows variants.
# Filesystem roots ("/", "C:\\") are caught by the anchor check below;
# this list is only the non-root sensitive directories.
_FORBIDDEN_VAULT_ROOTS = (
    # Unix system directories
    "/etc",
    "/root",
    "/boot",
    "/sys",
    "/proc",
    "/dev",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    # Windows system directories
    "C:\\Windows",
    "C:\\Program Files",
    "C:\\Program Files (x86)",
    "C:\\ProgramData",
)


def _validate_vault_path(vault_path: str) -> Path:
    """Validate and resolve a vault path for use by the MCP server.

    Rejects:
      * Empty or non-string input.
      * Paths containing ``..`` components (checked pre-resolve so symlink
        tricks can't hide the token).
      * Paths that resolve to a filesystem root (``/``, ``C:\\``, etc.).
      * Paths that equal or are contained within a known-sensitive system
        directory (``/etc``, ``C:\\Windows``, etc.).

    Returns the resolved Path. Raises ValueError on rejection.
    """
    if not vault_path or not isinstance(vault_path, str):
        raise ValueError("vault_path must be a non-empty string")
    if ".." in Path(vault_path).parts:
        raise ValueError("vault_path must not contain '..'")
    try:
        resolved = Path(vault_path).expanduser().resolve()
    except (OSError, RuntimeError) as e:
        raise ValueError(f"vault_path could not be resolved: {e}") from e

    # Filesystem root: anchor equals the whole resolved path.
    if resolved == Path(resolved.anchor):
        raise ValueError(f"vault_path cannot be a filesystem root: {resolved}")

    resolved_norm = str(resolved).replace("\\", "/").lower().rstrip("/")
    for forbidden in _FORBIDDEN_VAULT_ROOTS:
        fr = forbidden.replace("\\", "/").lower().rstrip("/")
        if resolved_norm == fr or resolved_norm.startswith(fr + "/"):
            raise ValueError(
                f"vault_path is in a restricted system directory: {resolved}"
            )
    return resolved


# ---------------------------------------------------------------------------
# Tool Implementations
# ---------------------------------------------------------------------------


def _load_core():
    """Lazy-load the core module."""
    from ompa import Ompa

    return Ompa


# Process-local cache of Ompa instances, keyed by the resolved vault paths +
# isolation mode + enable_semantic flag. Constructing an Ompa with
# enable_semantic=True is expensive (sentence-transformers model load,
# semantic index parse) so we reuse the same instance across tool calls
# within a single MCP server process. This turned ao_search from
# ~several-seconds-per-call to ~milliseconds after the first warm-up.
_OMPA_CACHE: dict[tuple, object] = {}


def _normalize_cache_path(path: str | None) -> str | None:
    """Normalize a path for cache keying — resolve + expanduser so that
    "./vault" and "/abs/path/vault" from different CWDs share a cache entry."""
    if path is None:
        return None
    try:
        return str(Path(path).expanduser().resolve())
    except (OSError, RuntimeError):
        return path


def _ompa_cache_key(
    vault_path: str | None,
    shared_vault_path: str | None,
    personal_vault_path: str | None,
    isolation_mode: str,
    enable_semantic: bool,
) -> tuple:
    return (
        _normalize_cache_path(vault_path),
        _normalize_cache_path(shared_vault_path),
        _normalize_cache_path(personal_vault_path),
        isolation_mode,
        bool(enable_semantic),
    )


def _get_ompa(
    vault_path: str | None = None,
    shared_vault_path: str | None = None,
    personal_vault_path: str | None = None,
    isolation_mode: str = "strict",
    enable_semantic: bool = False,
):
    """Return a cached Ompa instance for the given vault configuration,
    creating one on first access. Subsequent calls with the same
    (vault_path, shared, personal, isolation, semantic) tuple reuse the
    same instance — avoiding repeated sentence-transformers loads."""
    AO = _load_core()
    key = _ompa_cache_key(
        vault_path,
        shared_vault_path,
        personal_vault_path,
        isolation_mode,
        enable_semantic,
    )
    cached = _OMPA_CACHE.get(key)
    if cached is not None:
        return cached

    if shared_vault_path and personal_vault_path:
        ao = AO(
            shared_vault_path=shared_vault_path,
            personal_vault_path=personal_vault_path,
            isolation_mode=isolation_mode,
            enable_semantic=enable_semantic,
        )
    else:
        ao = AO(vault_path=vault_path or ".", enable_semantic=enable_semantic)

    _OMPA_CACHE[key] = ao
    return ao


def _clear_ompa_cache() -> None:
    """Drop the process-local Ompa cache. Primarily for tests that rapidly
    create throwaway vaults in temp dirs — without this, stale cached
    instances would point at deleted directories across tests."""
    _OMPA_CACHE.clear()


def _make_ompa(arguments: dict, enable_semantic: bool = False):
    """Create an Ompa instance from MCP arguments, supporting dual vault.

    Uses the process-local Ompa cache (see ``_get_ompa``) so repeated calls
    with the same vault configuration reuse a single instance.
    """
    vault_path = str(arguments.get("vault_path", "."))
    shared_vault = arguments.get("shared_vault_path")
    personal_vault = arguments.get("personal_vault_path")
    isolation = arguments.get("isolation_mode", "strict")
    return _get_ompa(
        vault_path=vault_path,
        shared_vault_path=shared_vault,
        personal_vault_path=personal_vault,
        isolation_mode=isolation,
        enable_semantic=enable_semantic,
    )


def ao_session_start(vault_path: str = ".") -> dict:
    """
    Start a session. Loads vault context: file listing, North Star,
    active work, palace wings, KG stats. ~2K tokens.
    """
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
    result = ao.session_start()
    return {
        "success": result.success,
        "output": result.output,
        "tokens": result.tokens_hint,
    }


def ao_classify(message: str, vault_path: str = ".") -> dict:
    """Classify a user message into one of 15 types."""
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
    c = ao.classify(message)
    return {
        "message_type": c.message_type.value,
        "confidence": c.confidence,
        "action": c.suggested_action,
        "routing_hints": c.routing_hints,
    }


def ao_search(query: str, vault_path: str = ".", limit: int = 5) -> dict:
    """Search the vault with hybrid semantic + keyword search."""
    ao = _get_ompa(vault_path=vault_path, enable_semantic=True)
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


def ao_kg_query(entity: str, vault_path: str = ".", as_of: str | None = None) -> dict:
    """Query the knowledge graph for an entity."""
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
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
    valid_from: str | None = None,
    source: str | None = None,
    vault_path: str = ".",
) -> dict:
    """
    Add a fact to the knowledge graph.
    """
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
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
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
    return ao.kg.stats()


def ao_palace_wings(vault_path: str = ".") -> dict:
    """List all palace wings."""
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
    return {"wings": ao.palace.list_wings()}


def ao_palace_rooms(wing: str, vault_path: str = ".") -> dict:
    """List rooms in a wing."""
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
    rooms = ao.palace.list_rooms(wing)
    return {"wing": wing, "rooms": rooms}


def ao_palace_tunnel(
    wing_a: str, wing_b: str, room: str, vault_path: str = "."
) -> dict:
    """Create a tunnel between two wings."""
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
    ao.palace.create_tunnel(wing_a, wing_b, room)
    return {"success": True, "tunnel": f"{wing_a} <-> {wing_b} via {room}"}


def ao_validate(file_path: str, vault_path: str = ".") -> dict:
    """Validate a markdown file."""
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
    return ao.validate_write(file_path)


def ao_wrap_up(vault_path: str = ".") -> dict:
    """Run session wrap-up."""
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
    result = ao.stop()
    return {"success": result.success, "output": result.output}


def ao_status(vault_path: str = ".") -> dict:
    """Get full status (vault + palace + KG)."""
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
    return {
        "vault": ao.get_stats(),
        "palace": ao.palace.stats(),
        "kg": ao.kg.stats(),
    }


def ao_orphans(vault_path: str = ".") -> dict:
    """Find orphan notes."""
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
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
    ao = _get_ompa(vault_path=vault_path, enable_semantic=False)
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
    ao = _get_ompa(vault_path=vault_path, enable_semantic=True)
    result = ao.sync()
    return {"success": True, **result}


def ao_write(arguments: dict) -> dict:
    """Write content to the appropriate vault (auto-classifies in dual mode)."""
    ao = _make_ompa(arguments, enable_semantic=False)
    content = arguments.get("content", "")
    # Accept tags as either a list (native MCP JSON) or a comma-separated
    # string (convenience for CLI-style callers). Strip + drop empties either way.
    tags_raw = arguments.get("tags", "")
    if isinstance(tags_raw, list):
        tags = [str(t).strip() for t in tags_raw if str(t).strip()]
    elif tags_raw:
        tags = [t.strip() for t in str(tags_raw).split(",") if t.strip()]
    else:
        tags = []
    result = ao.write(
        content,
        file_path=arguments.get("file_path"),
        tags=tags,
        vault=arguments.get("vault"),
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


# ---------------------------------------------------------------------------
# Dispatch registry: tool name -> callable(vault_path, arguments) -> result.
# Replaces the 18-arm if/elif chain with a data-driven lookup.  To add a
# new tool: (1) define the handler function above, (2) add a TOOLS entry,
# (3) add a _DISPATCH entry.
# ---------------------------------------------------------------------------

_DISPATCH = {
    "ao_session_start": lambda vp, a: ao_session_start(vp),
    "ao_classify": lambda vp, a: ao_classify(message=a["message"], vault_path=vp),
    "ao_search": lambda vp, a: ao_search(
        query=a["query"], vault_path=vp, limit=a.get("limit", 5)
    ),
    "ao_kg_query": lambda vp, a: ao_kg_query(
        entity=a["entity"], vault_path=vp, as_of=a.get("as_of")
    ),
    "ao_kg_add": lambda vp, a: ao_kg_add(
        subject=a["subject"],
        predicate=a["predicate"],
        object_=a["object"],
        valid_from=a.get("valid_from"),
        source=a.get("source"),
        vault_path=vp,
    ),
    "ao_kg_stats": lambda vp, a: ao_kg_stats(vp),
    "ao_palace_wings": lambda vp, a: ao_palace_wings(vp),
    "ao_palace_rooms": lambda vp, a: ao_palace_rooms(wing=a["wing"], vault_path=vp),
    "ao_palace_tunnel": lambda vp, a: ao_palace_tunnel(
        wing_a=a["wing_a"],
        wing_b=a["wing_b"],
        room=a.get("room", "shared"),
        vault_path=vp,
    ),
    "ao_validate": lambda vp, a: ao_validate(file_path=a["file_path"], vault_path=vp),
    "ao_wrap_up": lambda vp, a: ao_wrap_up(vp),
    "ao_status": lambda vp, a: ao_status(vp),
    "ao_orphans": lambda vp, a: ao_orphans(vp),
    "ao_kg_populate": lambda vp, a: ao_kg_populate(vp),
    "ao_sync": lambda vp, a: ao_sync(vp),
    "ao_write": lambda vp, a: ao_write(a),
    "ao_export": lambda vp, a: ao_export(a),
    "ao_import": lambda vp, a: ao_import(a),
    "ao_init": lambda vp, a: ao_init(vp),
}


def handle_call_tool(name: str, arguments: dict) -> dict:
    """Handle tool call request.

    Uses :data:`_DISPATCH` for routing instead of a long if/elif chain.
    Full exceptions are logged server-side (stderr); the caller receives
    a sanitized ``{error: ...}`` dict without internal stack details.
    """
    if name not in TOOLS:
        return {"error": f"Unknown tool: {name}"}

    try:
        # Extract and validate vault_path (and any dual-vault paths).
        vault_path = str(arguments.get("vault_path", "."))
        try:
            _validate_vault_path(vault_path)
        except ValueError as e:
            return {"error": f"Invalid vault_path: {e}"}
        for key in ("shared_vault_path", "personal_vault_path"):
            val = arguments.get(key)
            if val:
                try:
                    _validate_vault_path(str(val))
                except ValueError as e:
                    return {"error": f"Invalid {key}: {e}"}
        # Cap limit parameters
        if "limit" in arguments:
            arguments = {**arguments, "limit": min(int(arguments["limit"]), 100)}

        handler = _DISPATCH.get(name)
        if handler is None:
            return {"error": f"Unhandled tool: {name}"}
        return handler(vault_path, arguments)

    except KeyError as e:
        return {"error": f"Missing required argument: {e}"}
    except Exception as e:
        logger.exception("Tool %r failed", name)
        return {"error": f"{type(e).__name__}: tool execution failed"}


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
            logger.exception("MCP request failed")
            req_id = None
            if request is not None:
                req_id = request.get("id") if isinstance(request, dict) else None
            error_response = {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {
                    "code": -32603,
                    "message": f"{type(e).__name__}: request failed",
                },
            }
            sys.stdout.write(json.dumps(error_response) + "\n")
            sys.stdout.flush()
            request = None  # Reset for next iteration


if __name__ == "__main__":
    main()
