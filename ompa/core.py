"""
AgnosticObsidian — Universal AI Agent Memory Layer
Core module integrating vault, palace, KG, hooks, classifier, and semantic search.
"""

from pathlib import Path
from typing import Optional

from .vault import Vault
from .palace import Palace
from .knowledge_graph import KnowledgeGraph
from .hooks import HookManager, HookResult
from .classifier import MessageClassifier, Classification
from .semantic import SemanticIndex, SearchResult


class AgnosticObsidian:
    """
    Universal agent memory layer.

    Integrates:
    - Vault (obsidian-mind conventions: brain/work/org/perf folders, wikilinks)
    - Palace (MemPalace wings/rooms/closets/drawers metadata layer)
    - Knowledge Graph (temporal SQLite triples with validity windows)
    - Hooks (session_start, user_message, post_tool, pre_compact, stop)
    - Classifier (15 message types with routing hints)
    - Semantic Search (local sentence-transformers)

    Usage:
        ao = AgnosticObsidian(vault_path="./workspace")
        result = ao.session_start()    # ~2K tokens
        hint = ao.handle_message(msg)  # ~100 tokens
        ao.post_tool("write", {"file_path": "work/active/auth.md"})
        ao.stop()
    """

    def __init__(
        self,
        vault_path: str | Path,
        agent_name: str = "agent",
        enable_semantic: bool = True,
    ):
        self.vault_path = Path(vault_path)
        self.agent_name = agent_name

        # Core systems
        self.vault = Vault(self.vault_path)
        self.palace = Palace(self.vault_path / ".palace")
        self.kg = KnowledgeGraph(
            db_path=str(self.vault_path / ".palace" / "knowledge_graph.sqlite3")
        )
        self.classifier = MessageClassifier()
        self.hooks = HookManager(self.vault_path, agent_name=self.agent_name)

        # Semantic search (lazy-loaded)
        self._semantic = None
        self._enable_semantic = enable_semantic

        self._session_started = False
        self._last_classification: Optional[Classification] = None

    @property
    def semantic(self) -> Optional[SemanticIndex]:
        """Lazy-load semantic index on first access."""
        if self._semantic is None and self._enable_semantic:
            self._semantic = SemanticIndex(
                index_path=self.vault_path / ".palace" / "semantic_index",
            )
            if not self._semantic.load_index():
                count = self._semantic.index_vault(self.vault_path)
                if count > 0:
                    self._semantic.save_index()
        return self._semantic

    # -------------------------------------------------------------------------
    # Lifecycle Hooks
    # -------------------------------------------------------------------------

    def session_start(self) -> HookResult:
        """
        Run session start hook.
        Loads ~2K tokens: vault listing, North Star, active work, palace wings, KG stats.
        """
        result = self.hooks.run_session_start(self)
        self._session_started = True
        return result

    def handle_message(self, message: str) -> HookResult:
        """
        Handle a user message.
        Classifies the message and returns routing hints (~100 tokens).
        """
        result = self.hooks.run_user_message(message, self)
        if result.success:
            self._last_classification = self.classifier.classify(message)
        return result

    def post_tool(self, tool_name: str, tool_input: dict) -> HookResult:
        """
        Run post-tool hook after tool use.
        Validates writes, checks wikilinks, auto-adds to palace.
        """
        result = self.hooks.run_post_tool(tool_name, tool_input, self)

        # Auto-add to palace on file writes
        if result.success and tool_name in ("write", "edit", "create_file"):
            file_path = tool_input.get("file_path") or tool_input.get("path")
            if file_path:
                self._auto_add_to_palace(file_path)

        return result

    def pre_compact(self, transcript: str) -> HookResult:
        """Run pre-compact hook before context compaction."""
        return self.hooks.run_pre_compact(transcript, self)

    def stop(self) -> HookResult:
        """Run stop hook (wrap-up checklist)."""
        result = self.hooks.run_stop(self)
        self._session_started = False
        return result

    def wrap_up(self) -> HookResult:
        """Alias for stop()."""
        return self.stop()

    def standup(self) -> HookResult:
        """Alias for session_start()."""
        return self.session_start()

    # -------------------------------------------------------------------------
    # Auto palace population
    # -------------------------------------------------------------------------

    def _auto_add_to_palace(self, file_path: str) -> None:
        """Auto-add a written file to the palace metadata layer."""
        path = Path(file_path)
        if path.suffix != ".md":
            return

        try:
            # Determine wing and room from path
            parts = path.parts
            if "brain" in parts:
                wing = "brain"
                room = path.stem.lower().replace(" ", "-")
            elif "work" in parts:
                wing = "work"
                room = path.stem.lower().replace(" ", "-")
            elif "org" in parts and "people" in parts:
                wing = path.stem  # person name
                room = "context"
            else:
                return

            self.palace.create_room(wing, room)
            self.palace.link_drawer(wing, room, str(path))
        except Exception:
            pass  # Silently skip palace errors

    # -------------------------------------------------------------------------
    # Classification
    # -------------------------------------------------------------------------

    def classify(self, message: str) -> Classification:
        """Classify a user message."""
        return self.classifier.classify(message)

    def get_routing_hint(self, message: str) -> str:
        """Get a one-line routing hint for a message."""
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
        wing: str = None,
        room: str = None,
    ) -> list[SearchResult]:
        """
        Search the vault semantically.

        Args:
            query: Search query
            limit: Max results
            hybrid: Use hybrid (semantic + keyword) search
            wing: Filter by palace wing
            room: Filter by palace room
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

        results = self.semantic.search(query, limit, hybrid)

        # Filter by palace wing/room if specified
        if wing or room:
            filtered = []
            for r in results:
                # Try to match palace metadata
                # (In production, would join with palace data)
                if wing and wing not in r.path:
                    continue
                if room and room not in r.path:
                    continue
                filtered.append(r)
            results = filtered or results[:limit]

        return results

    def qsearch(self, query: str, limit: int = 5) -> list[SearchResult]:
        """QMD-style semantic search. Convenience method."""
        return self.search(query, limit, hybrid=True)

    def rebuild_index(self) -> int:
        """Rebuild the semantic index."""
        if self.semantic is None:
            return 0
        self._semantic.clear()
        count = self._semantic.index_vault(self.vault_path)
        self._semantic.save_index()
        return count

    # -------------------------------------------------------------------------
    # Validation
    # -------------------------------------------------------------------------

    def validate_write(self, file_path: str) -> dict:
        """Validate a markdown file for frontmatter and wikilinks."""
        return self.vault.validate_write(file_path)

    # -------------------------------------------------------------------------
    # Vault Management
    # -------------------------------------------------------------------------

    def get_stats(self) -> dict:
        """Get vault statistics."""
        return self.vault.get_stats()

    def find_orphans(self) -> list:
        """Find notes with no wikilinks."""
        return self.vault.find_orphans()

    def update_brain(self, note_name: str, content: str, append: bool = False) -> None:
        """Update a brain note."""
        self.vault.update_brain_note(note_name, content, append)

    def get_brain_note(self, name: str) -> Optional[object]:
        """Get a brain note by name."""
        return self.vault.get_brain_note(name)

    # -------------------------------------------------------------------------
    # Palace shortcuts
    # -------------------------------------------------------------------------

    def palace_build(self) -> int:
        """Auto-build palace metadata from vault structure."""
        return self.palace.auto_build_from_vault(self.vault_path)

    # -------------------------------------------------------------------------
    # KG shortcuts
    # -------------------------------------------------------------------------

    def kg_add(
        self,
        subject: str,
        predicate: str,
        object: str,
        valid_from: str = None,
        source: str = None,
    ) -> None:
        """Add a fact to the knowledge graph."""
        self.kg.add_triple(
            subject, predicate, object, valid_from=valid_from, source=source
        )

    def kg_query(self, entity: str, as_of: str = None) -> list:
        """Query the knowledge graph."""
        return self.kg.query_entity(entity, as_of=as_of)

    def kg_timeline(self, entity: str) -> list:
        """Get entity timeline."""
        return self.kg.timeline(entity)
