"""
OMPA — Obsidian-MemPalace-Agnostic

Universal AI agent memory layer.
Combines obsidian-mind vault conventions + MemPalace palace structure + temporal knowledge graph.

Works with OpenClaw, Claude Code, Codex, Gemini CLI, or any AI agent.

Usage:
    from ompa import Ompa
    ao = Ompa(vault_path="./workspace")
    result = ao.session_start()
    hint = ao.handle_message("We decided to go with Postgres")
    ao.post_tool("write", {"file_path": "work/active/auth.md"})
    ao.stop()
"""

__version__ = "0.2.2"

from .core import Ompa
from .vault import Vault, Note, VaultConfig
from .palace import Palace
from .knowledge_graph import KnowledgeGraph
from .classifier import MessageClassifier, Classification, MessageType
from .hooks import HookManager, HookContext, HookResult, Hook
from .semantic import SemanticIndex, SearchResult

# Backward compatibility alias
AgnosticObsidian = Ompa

__all__ = [
    "Ompa",
    "AgnosticObsidian",  # backward compat
    "Vault",
    "Note",
    "VaultConfig",
    "Palace",
    "KnowledgeGraph",
    "MessageClassifier",
    "Classification",
    "MessageType",
    "HookManager",
    "HookContext",
    "HookResult",
    "Hook",
    "SemanticIndex",
    "SearchResult",
]
