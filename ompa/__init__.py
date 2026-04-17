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

# Version is resolved from the installed package metadata (pyproject.toml is
# the single source of truth). Falls back to "0.0.0+unknown" for editable
# installs or source-tree imports where the distribution isn't registered.
try:
    from importlib.metadata import PackageNotFoundError
    from importlib.metadata import version as _pkg_version

    try:
        __version__ = _pkg_version("ompa")
    except PackageNotFoundError:
        __version__ = "0.0.0+unknown"
    del _pkg_version, PackageNotFoundError
except ImportError:  # pragma: no cover — importlib.metadata is stdlib ≥3.8
    __version__ = "0.0.0+unknown"

from .classifier import Classification, MessageClassifier, MessageType
from .config import DualVaultConfig, IsolationMode, VaultTarget
from .core import Ompa
from .hooks import Hook, HookContext, HookManager, HookResult
from .knowledge_graph import KnowledgeGraph
from .palace import Palace
from .semantic import SearchResult, SemanticIndex
from .vault import Note, Vault, VaultConfig

# Backward compatibility alias
AgnosticObsidian = Ompa

__all__ = [
    "AgnosticObsidian",  # backward compat
    "Classification",
    "DualVaultConfig",
    "Hook",
    "HookContext",
    "HookManager",
    "HookResult",
    "IsolationMode",
    "KnowledgeGraph",
    "MessageClassifier",
    "MessageType",
    "Note",
    "Ompa",
    "Palace",
    "SearchResult",
    "SemanticIndex",
    "Vault",
    "VaultConfig",
    "VaultTarget",
]
