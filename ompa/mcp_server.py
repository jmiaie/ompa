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

import contextvars
import json
import os
import sys
from pathlib import Path

from ompa import Ompa as _Ompa
from ompa import __version__
from ompa.config import make_ompa
from ompa.governance import AccessDeniedError, AccessPolicy, LegalHoldError

# Principal for the tool call being handled.  If the server operator pins one
# with $OMPA_PRINCIPAL, a client-supplied ``principal`` argument that differs is
# rejected (MCP stdio has no authentication; see docs/AUDIT.md threat model).
_PRINCIPAL: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ompa_principal", default=None
)


def Ompa(*args, **kwargs) -> _Ompa:  # noqa: N802 - drop-in for the class inside tools
    """Construct :class:`ompa.Ompa` acting as the current call's principal."""
    kwargs.setdefault("principal", _PRINCIPAL.get())
    return _Ompa(*args, **kwargs)


def _make_ompa(arguments: dict, enable_semantic: bool = False) -> _Ompa:
    return make_ompa(
        vault_path=arguments.get("vault_path", "."),
        shared_vault_path=arguments.get("shared_vault_path"),
        personal_vault_path=arguments.get("personal_vault_path"),
        enable_semantic=enable_semantic,
        principal=_PRINCIPAL.get(),
    )


def _resolve_principal(arguments: dict) -> str | None:
    pinned = os.environ.get("OMPA_PRINCIPAL")
    asked = arguments.get("principal")
    if pinned and asked and asked != pinned:
        raise AccessDeniedError(f"principal {asked!r} does not match the server's pinned principal")
    return pinned or asked


# Permission each tool needs on its vault when the vault has an acl.json.
# Dual-vault tools (ao_write / ao_export / ao_import) are checked per vault by
# Ompa itself.  Denials are recorded in the vault's audit chain.
TOOL_PERMS = {
    "ao_session_start": "read",
    "ao_search": "read",
    "ao_kg_query": "read",
    "ao_kg_stats": "read",
    "ao_palace_wings": "read",
    "ao_palace_rooms": "read",
    "ao_validate": "read",
    "ao_status": "read",
    "ao_orphans": "read",
    "ao_classify": "read",
    "ao_audit_verify": "read",
    "ao_audit_head": "read",
    "ao_audit_export": "read",
    "ao_as_of": "read",
    "ao_write": "write",
    "ao_export": "write",
    "ao_import": "write",
    "ao_kg_add": "write",
    "ao_kg_populate": "write",
    "ao_palace_tunnel": "write",
    "ao_wrap_up": "write",
    "ao_sync": "write",
    "ao_init": "write",
    "ao_forget": "admin",
}


def _enforce_acl(name: str, vault_path: str) -> None:
    perm = TOOL_PERMS.get(name)
    if perm is None or not (Path(vault_path) / ".palace" / "acl.json").exists():
        return
    policy = AccessPolicy.load(Path(vault_path) / ".palace" / "acl.json")
    if policy is not None and policy.allows(_PRINCIPAL.get() or "", perm):
        return
    # logs access.denied to the chain, then raises
    Ompa(vault_path=vault_path, enable_semantic=False).gov.require(perm, "mcp:" + name)


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
                "citation": r.citation,
            }
            for r in results
        ]
    }


def ao_kg_query(entity: str, vault_path: str = ".", as_of: str = None) -> dict:
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
                "citation": t.citation,
            }
            for t in triples
        ],
    }


def ao_audit_verify(vault_path: str = ".", key_path: str | None = None) -> dict:
    """Verify the vault's tamper-evident audit chain (+ note drift)."""
    verifier = None
    if key_path:
        from ompa.audit import load_key

        verifier = load_key(key_path)
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    return ao.audit_verify(verifier=verifier)


def ao_audit_head(vault_path: str = ".") -> dict:
    """Current chain head (store it elsewhere to anchor future verification)."""
    return Ompa(vault_path=vault_path, enable_semantic=False).audit_head()


def ao_audit_export(vault_path: str = ".", start: int = 1, end: int | None = None) -> dict:
    """Evidence bundle for entries start..end (capped at 10k entries per call)."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    if end is None or end - start + 1 > 10000:
        end = start + 9999
    return ao.audit_export(start=start, end=end)


def ao_forget(
    subject: str, vault_path: str = ".", aliases: list | None = None, dry_run: bool = True
) -> dict:
    """Right to erasure.  Defaults to a dry run; pass dry_run=false to erase."""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    return {"receipts": ao.forget([subject, *(aliases or [])], dry_run=dry_run)}


def ao_as_of(when: str, entity: str, vault_path: str = ".", valid_at: str | None = None) -> dict:
    """What did the agent know about an entity at transaction time `when`?"""
    ao = Ompa(vault_path=vault_path, enable_semantic=False)
    with ao.as_of(when) as view:
        return {
            "stats": view.stats(),
            "facts": [
                {
                    "subject": f.subject,
                    "predicate": f.predicate,
                    "object": f.object,
                    "citation": f.citation,
                }
                for f in view.facts(entity, valid_at=valid_at)
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


def ao_palace_tunnel(wing_a: str, wing_b: str, room: str, vault_path: str = ".") -> dict:
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
    content = arguments.get("content", "")
    tags_raw = arguments.get("tags", "")
    tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
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
    "ao_audit_verify": {
        "description": "Verify the tamper-evident audit log: hash chain, Merkle checkpoints, "
        "head, signatures (with key_path) and note drift. Detects edits, deletions, "
        "reorders, insertions and truncation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
                "key_path": {"type": "string", "description": "Public/HMAC key file"},
            },
        },
    },
    "ao_audit_head": {
        "description": "Current audit chain head {seq, hash}; store it outside the vault "
        "as an anchor.",
        "input_schema": {
            "type": "object",
            "properties": {"vault_path": {"type": "string", "default": "."}},
        },
    },
    "ao_audit_export": {
        "description": "Export an evidence bundle (JSON) of audit entries start..end.",
        "input_schema": {
            "type": "object",
            "properties": {
                "vault_path": {"type": "string", "default": "."},
                "start": {"type": "integer", "default": 1},
                "end": {"type": "integer"},
            },
        },
    },
    "ao_forget": {
        "description": "Right to erasure (admin): delete/redact a subject from notes, "
        "facts, indexes and audit bodies, keeping a tombstone. dry_run defaults to true.",
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {"type": "string"},
                "aliases": {"type": "array", "items": {"type": "string"}},
                "dry_run": {"type": "boolean", "default": True},
                "vault_path": {"type": "string", "default": "."},
            },
            "required": ["subject"],
        },
    },
    "ao_as_of": {
        "description": "Point-in-time view: facts known about an entity at time `when` "
        "(ISO 8601, UTC if no offset), replayed from the audit log.",
        "input_schema": {
            "type": "object",
            "properties": {
                "when": {"type": "string"},
                "entity": {"type": "string"},
                "valid_at": {"type": "string", "description": "Valid-time date"},
                "vault_path": {"type": "string", "default": "."},
            },
            "required": ["when", "entity"],
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
        schema = json.loads(json.dumps(spec["input_schema"]))
        schema.setdefault("properties", {})["principal"] = {
            "type": "string",
            "description": "User/agent id to act as (ACL + audit actor); ignored "
            "if the server pins $OMPA_PRINCIPAL",
        }
        tools.append(
            {
                "name": name,
                "description": spec["description"],
                "inputSchema": schema,
            }
        )
    return {"tools": tools}


def handle_call_tool(name: str, arguments: dict) -> dict:
    """Handle tool call request."""
    if name not in TOOLS:
        return {"error": f"Unknown tool: {name}"}

    try:
        # Extract and validate vault_path
        vault_path = str(arguments.get("vault_path", "."))
        # Block obvious traversal attempts
        if ".." in vault_path or vault_path in ("/", "C:\\", "C:/"):
            return {"error": "Invalid vault_path"}
        # Cap limit parameters
        if "limit" in arguments:
            arguments = {**arguments, "limit": min(int(arguments["limit"]), 100)}
        _PRINCIPAL.set(_resolve_principal(arguments))
        _enforce_acl(name, vault_path)

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
        elif name == "ao_kg_populate":
            result = ao_kg_populate(vault_path)
        elif name == "ao_sync":
            result = ao_sync(vault_path)
        elif name == "ao_write":
            result = ao_write(arguments)
        elif name == "ao_export":
            result = ao_export(arguments)
        elif name == "ao_import":
            result = ao_import(arguments)
        elif name == "ao_init":
            result = ao_init(vault_path)
        elif name == "ao_audit_verify":
            result = ao_audit_verify(vault_path, arguments.get("key_path"))
        elif name == "ao_audit_head":
            result = ao_audit_head(vault_path)
        elif name == "ao_audit_export":
            result = ao_audit_export(
                vault_path, int(arguments.get("start", 1)), arguments.get("end")
            )
        elif name == "ao_forget":
            result = ao_forget(
                arguments["subject"],
                vault_path,
                arguments.get("aliases"),
                bool(arguments.get("dry_run", True)),
            )
        elif name == "ao_as_of":
            result = ao_as_of(
                arguments["when"], arguments["entity"], vault_path, arguments.get("valid_at")
            )
        else:
            result = {"error": f"Unhandled tool: {name}"}

        return result
    except (AccessDeniedError, LegalHoldError) as e:
        return {"error": type(e).__name__, "detail": str(e)}
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
