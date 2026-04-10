"""
OMPA — Obsidian-MemPalace-Agnostic

Universal AI agent memory layer.
Combines obsidian-mind vault conventions + MemPalace palace structure + temporal knowledge graph.

Works with OpenClaw, Claude Code, Codex, Gemini CLI, or any AI agent.

Usage:
    from ompa import AgnosticObsidian
    ao = AgnosticObsidian(vault_path="./workspace")
    result = ao.session_start()
    hint = ao.handle_message("We decided to go with Postgres")
    ao.post_tool("write", {"file_path": "work/active/auth.md"})
    ao.stop()
"""

__version__ = "0.1.0"

from .core import AgnosticObsidian
from .vault import Vault, Note, VaultConfig
from .palace import Palace
from .knowledge_graph import KnowledgeGraph
from .classifier import MessageClassifier, Classification, MessageType
from .hooks import HookManager, HookContext, HookResult, Hook
from .semantic import SemanticIndex, SearchResult

__all__ = [
    "AgnosticObsidian",
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
