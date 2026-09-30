"""
OMPA — Obsidian-MemPalace-Agnostic

Universal AI agent memory layer.
Combines obsidian-mind vault conventions + MemPalace palace structure + temporal knowledge graph.

Works with any AI agent: Claude Code, OpenClaw, Codex, Gemini CLI, LangChain, LlamaIndex,
OpenAI Agents SDK, or any custom agent runtime.

Usage:
    from ompa import Ompa
    ao = Ompa(vault_path="./workspace")
    result = ao.session_start()
    hint = ao.handle_message("We decided to go with Postgres")
    ao.post_tool("write", {"file_path": "work/active/auth.md"})
    ao.stop()

Framework adapters:
    from ompa.adapters.langchain import OmpaMemory, OmpaRetriever
    from ompa.adapters.llamaindex import OmpaReader, OmpaVaultRetriever
    from ompa.adapters.openai_agents import OmpaAgentHooks
    from ompa.adapters.nim import NIMEmbeddingBackend

Sync backends:
    from ompa.sync import GitSyncBackend, S3SyncBackend, RsyncBackend
"""

__version__ = "1.0.8"

from .async_api import AsyncOmpa
from .classifier import Classification, MessageClassifier, MessageType
from .config import DualVaultConfig, IsolationMode, VaultTarget
from .core import Ompa
from .hooks import Hook, HookContext, HookManager, HookResult
from .knowledge_graph import KnowledgeGraph
from .palace import Palace
from .semantic import SearchResult, SemanticIndex
from .token_counter import count_tokens
from .vault import Note, Vault, VaultConfig

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
    "DualVaultConfig",
    "IsolationMode",
    "VaultTarget",
    "count_tokens",
    "AsyncOmpa",
]
