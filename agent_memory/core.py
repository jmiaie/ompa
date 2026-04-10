"""
Main AgentMemory class - the primary interface for agent memory.
"""
import os
from pathlib import Path
from datetime import datetime
from typing import Optional

from .vault import Vault
from .hooks import HookManager, HookResult
from .classifier import MessageClassifier, Classification
from .semantic import SemanticIndex, SearchResult


class AgentMemory:
    """
    Universal agent memory layer.
    
    Provides persistent memory across sessions with:
    - Lifecycle hooks (session_start, user_message, post_tool, pre_compact, stop)
    - Semantic search across vault
    - Message classification and routing
    - Structured vault management
    
    Usage:
        memory = AgentMemory(vault_path="./workspace")
        
        # At session start
        memory.session_start()
        
        # On every user message
        context_hint = memory.handle_message("user message here")
        
        # After tool use
        memory.post_tool("write", {"file_path": "work/active/test.md"})
        
        # At session end
        memory.stop()
    """
    
    def __init__(
        self,
        vault_path: str | Path,
        agent_name: str = "agent",
        enable_semantic: bool = True,
    ):
        self.vault_path = Path(vault_path)
        self.agent_name = agent_name
        self.vault = Vault(self.vault_path)
        self.hooks = HookManager(self.vault_path)
        self.classifier = MessageClassifier()
        self.semantic = None
        
        if enable_semantic:
            self.semantic = SemanticIndex(
                index_path=self.vault_path / ".agent-memory",
            )
            # Try to load existing index
            if not self.semantic.load_index():
                # Build index if none exists
                count = self.semantic.index_vault(self.vault_path)
                if count > 0:
                    self.semantic.save_index()
        
        self._session_started = False
        self._last_classification: Optional[Classification] = None
    
    # -------------------------------------------------------------------------
    # Lifecycle Hooks
    # -------------------------------------------------------------------------
    
    def session_start(self) -> HookResult:
        """
        Run session start hook.
        Call this at the beginning of each session.
        
        Returns HookResult with context summary for injection.
        """
        result = self.hooks.run_session_start(self)
        self._session_started = True
        return result
    
    def handle_message(self, message: str) -> HookResult:
        """
        Handle a user message.
        Classifies the message and returns routing hints.
        
        Args:
            message: The user message
            
        Returns:
            HookResult with classification and routing guidance
        """
        result = self.hooks.run_user_message(message, self)
        if result.success:
            # Also return the classification
            self._last_classification = self.classifier.classify(message)
        return result
    
    def post_tool(self, tool_name: str, tool_input: dict) -> HookResult:
        """
        Run post-tool hook after tool use.
        Validates writes, checks wikilinks.
        
        Args:
            tool_name: Name of the tool used
            tool_input: Tool input dict
        """
        return self.hooks.run_post_tool(tool_name, tool_input, self)
    
    def pre_compact(self, transcript: str) -> HookResult:
        """
        Run pre-compact hook before context compaction.
        Archives session transcript.
        
        Args:
            transcript: Session transcript so far
        """
        return self.hooks.run_pre_compact(transcript, self)
    
    def stop(self) -> HookResult:
        """
        Run stop hook (wrap up).
        Verifies notes, updates indexes, spots wins.
        """
        result = self.hooks.run_stop(self)
        self._session_started = False
        return result
    
    # -------------------------------------------------------------------------
    # Classification
    # -------------------------------------------------------------------------
    
    def classify(self, message: str) -> Classification:
        """
        Classify a user message.
        
        Args:
            message: The message to classify
            
        Returns:
            Classification with type, confidence, routing hints
        """
        return self.classifier.classify(message)
    
    def get_routing_hint(self, message: str) -> str:
        """
        Get a one-line routing hint for a message.
        
        Args:
            message: The message to classify
            
        Returns:
            Single line routing hint
        """
        return self.classifier.get_routing_hint(message)
    
    @property
    def last_classification(self) -> Optional[Classification]:
        """Get the last classification result."""
        return self._last_classification
    
    # -------------------------------------------------------------------------
    # Search
    # -------------------------------------------------------------------------
    
    def search(
        self,
        query: str,
        limit: int = 5,
        hybrid: bool = True,
    ) -> list[SearchResult]:
        """
        Search the vault semantically.
        
        Args:
            query: Search query
            limit: Max results
            hybrid: Use hybrid (semantic + keyword) search
        
        Returns:
            List of SearchResult
        """
        if self.semantic is None:
            # Fallback to name search
            notes = self.vault.search_by_name(query)
            return [
                SearchResult(
                    path=str(n.path),
                    content_excerpt=n.content[:200],
                    score=1.0,
                    match_type="name",
                )
                for n in notes[:limit]
            ]
        
        return self.semantic.search(query, limit, hybrid)
    
    def qsearch(self, query: str, limit: int = 5) -> list[SearchResult]:
        """
        QMD-style semantic search.
        Convenience method for `search()`.
        """
        return self.search(query, limit, hybrid=True)
    
    def rebuild_index(self) -> int:
        """
        Rebuild the semantic index.
        
        Returns:
            Number of files indexed
        """
        if self.semantic is None:
            return 0
        
        self.semantic.clear()
        count = self.semantic.index_vault(self.vault_path)
        self.semantic.save_index()
        return count
    
    # -------------------------------------------------------------------------
    # Vault Management
    # -------------------------------------------------------------------------
    
    def get_stats(self) -> dict:
        """Get vault statistics."""
        return self.vault.get_stats()
    
    def find_orphans(self) -> list:
        """Find notes with no links."""
        return self.vault.find_orphans()
    
    def update_brain(self, note_name: str, content: str, append: bool = False) -> None:
        """
        Update a brain note.
        
        Args:
            note_name: Brain note name (e.g., "Key Decisions")
            content: Content to write or append
            append: If True, append to existing content
        """
        self.vault.update_brain_note(note_name, content, append)
    
    def get_brain_note(self, name: str) -> Optional[object]:
        """Get a brain note by name."""
        return self.vault.get_brain_note(name)
    
    def create_note(self, template_name: str, target_name: str, **kwargs) -> None:
        """
        Create a note from a template.
        
        Args:
            template_name: Template name (e.g., "Decision Record")
            target_name: Target file path (e.g., "work/active/ADR-001.md")
            **kwargs: Template placeholders
        """
        self.vault.create_from_template(template_name, target_name, **kwargs)
    
    # -------------------------------------------------------------------------
    # Convenience
    # -------------------------------------------------------------------------
    
    def wrap_up(self) -> HookResult:
        """
        Alias for stop() - wrap up the session.
        """
        return self.stop()
    
    def standup(self) -> HookResult:
        """
        Run standup context.
        """
        return self.session_start()
