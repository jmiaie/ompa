"""
OMPA MCP Tool implementations.

Contains the Python functions that back each MCP tool.  The MCP server
(mcp_server.py) imports these, defines the JSON-Schema descriptors, and
dispatches incoming tool-call requests to the appropriate function.

Keeping implementations here makes them independently testable and keeps
mcp_server.py focused on the JSON-RPC protocol layer.
"""

from pathlib import Path
from typing import Optional

from ompa import Ompa, __version__  # noqa: F401 — __version__ re-exported for server
from ompa.config import make_ompa


def _make_ompa(arguments: dict, enable_semantic: bool = False) -> Ompa:
    return make_ompa(
        vault_path=arguments.get("vault_path", "."),
        shared_vault_path=arguments.get("shared_vault_path"),
        personal_vault_path=arguments.get("personal_vault_path"),
        enable_semantic=enable_semantic,
    )


# ---------------------------------------------------------------------------
# Individual tool implementations
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
    """Add a fact to the knowledge graph."""
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
    """Full sync: rebuild KG, palace, and search index from vault."""
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
