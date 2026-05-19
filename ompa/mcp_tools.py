"""
OMPA MCP Tool Schema Registry.

Defines the JSON-schema metadata for every MCP tool exposed by the OMPA server.
Imported by mcp_server.py; kept separate so the schema data doesn't clutter the
protocol/dispatch logic.
"""

TOOLS: dict[str, dict] = {
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
