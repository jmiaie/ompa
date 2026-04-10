"""
AgentMemory - Universal Agent Memory Layer

A persistent memory system for AI agents that provides:
- Lifecycle hooks (session_start, user_message, post_tool, pre_compact, stop)
- Semantic memory with QMD-style search
- Tiered context loading for token efficiency
- Classification routing for incoming messages
- Structured vault organization

Works with any AI agent framework via Python API or CLI.

Usage:
    from agent_memory import AgentMemory
    memory = AgentMemory(vault_path="./workspace")
    memory.session_start()
    # ... agent work ...
    memory.handle_message("wrap up")
    memory.stop()
"""

__version__ = "0.1.0"

from .core import AgentMemory
from .vault import Vault, Note, VaultConfig
from .classifier import MessageClassifier, Classification, MessageType
from .hooks import HookManager, HookContext, HookResult, Hook
from .semantic import SemanticIndex, SearchResult

__all__ = [
    "AgentMemory",
    "Vault",
    "Note", 
    "VaultConfig",
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
