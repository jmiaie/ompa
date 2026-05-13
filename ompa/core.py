"""
OMPA — Universal AI Agent Memory Layer
Core module integrating vault, palace, KG, hooks, classifier, and semantic search.
Supports single-vault and dual-vault (shared + personal) architecture.

Dual-vault cross-transfer operations (write, export_to_shared, import_to_personal,
migrate_to_dual_vault) live in ompa/dual_vault_ops.py as DualVaultMixin.
"""

import logging
from pathlib import Path
from typing import Optional

from .vault import Vault, Note, _safe_resolve
from .palace import Palace
from .knowledge_graph import KnowledgeGraph
from .hooks import HookManager, HookResult
from .classifier import MessageClassifier, Classification
from .semantic import SemanticIndex, SearchResult
from .config import DualVaultConfig, IsolationMode, VaultTarget
from .dual_vault_ops import DualVaultMixin

logger = logging.getLogger(__name__)


class Ompa(DualVaultMixin):
    """
    Universal agent memory layer.

    Integrates:
    - Vault (obsidian-mind conventions: brain/work/org/perf folders, wikilinks)
    - Palace (MemPalace wings/rooms/closets/drawers metadata layer)
    - Knowledge Graph (temporal SQLite triples with validity windows)
    - Hooks (session_start, user_message, post_tool, pre_compact, stop)
    - Classifier (15 message types with routing hints)
    - Semantic Search (local sentence-transformers)

    Usage (single vault):
        ao = Ompa(vault_path="./workspace")

    Usage (dual vault):
        ao = Ompa(
            shared_vault_path="/shared/ompa-vault",
            personal_vault_path="~/.ompa-personal",
            isolation_mode="strict",
        )
    """

    def __init__(
        self,
        vault_path: Optional[str | Path] = None,
        agent_name: str = "agent",
        enable_semantic: bool = True,
        embedding_backend=None,  # EmbeddingBackend protocol — e.g. NIMEmbeddingBackend
        # Dual-vault parameters
        shared_vault_path: Optional[str | Path] = None,
        personal_vault_path: Optional[str | Path] = None,
        isolation_mode: str = "strict",
    ):
        self.agent_name = agent_name
        self._enable_semantic = enable_semantic
        self._embedding_backend = embedding_backend  # optional custom backend
        self._session_started = False
        self._last_classification: Optional[Classification] = None

        self.dual_config = DualVaultConfig(
            isolation_mode=IsolationMode(isolation_mode),
        )

        # Declare Optional personal-vault attributes unconditionally so mypy
        # sees a single consistent type regardless of which branch runs.
        self.personal_vault: Optional[Vault] = None
        self.personal_palace: Optional[Palace] = None
        self.personal_kg: Optional[KnowledgeGraph] = None

        if shared_vault_path and personal_vault_path:
            # Dual-vault mode
            self.dual_config.shared_path = Path(shared_vault_path).expanduser()
            self.dual_config.personal_path = Path(personal_vault_path).expanduser()
            self.vault_path = self.dual_config.shared_path  # primary for hooks

            self.vault = Vault(self.dual_config.shared_path)
            self.palace = Palace(self.dual_config.shared_path / ".palace")
            self.kg = KnowledgeGraph(
                db_path=str(
                    self.dual_config.shared_path / ".palace" / "knowledge_graph.sqlite3"
                )
            )

            self.personal_vault = Vault(self.dual_config.personal_path)
            self.personal_palace = Palace(self.dual_config.personal_path / ".palace")
            self.personal_kg = KnowledgeGraph(
                db_path=str(
                    self.dual_config.personal_path
                    / ".palace"
                    / "knowledge_graph.sqlite3"
                )
            )
        else:
            # Single-vault mode
            self.vault_path = Path(vault_path or ".")
            self.vault = Vault(self.vault_path)
            self.palace = Palace(self.vault_path / ".palace")
            self.kg = KnowledgeGraph(
                db_path=str(self.vault_path / ".palace" / "knowledge_graph.sqlite3")
            )

        self.classifier = MessageClassifier()
        self.hooks = HookManager(self.vault_path, agent_name=self.agent_name)

        # Explicit type annotation needed for mypy to narrow Optional[SemanticIndex]
        self._semantic: Optional[SemanticIndex] = None
        self._personal_semantic: Optional[SemanticIndex] = None

    @property
    def is_dual_vault(self) -> bool:
        """True if dual-vault mode is active."""
        return self.dual_config.is_dual_vault

    @property
    def semantic(self) -> Optional[SemanticIndex]:
        if self._semantic is None and self._enable_semantic:
            self._semantic = SemanticIndex(
                index_path=self.vault_path / ".palace" / "semantic_index",
                embedding_backend=self._embedding_backend,
            )
            if not self._semantic.load_index():
                count = self._semantic.index_vault(self.vault_path)
                if count > 0:
                    self._semantic.save_index()
        return self._semantic

    @property
    def personal_semantic(self) -> Optional[SemanticIndex]:
        if (
            self._personal_semantic is None
            and self._enable_semantic
            and self.dual_config.personal_path
        ):
            self._personal_semantic = SemanticIndex(
                index_path=self.dual_config.personal_path
                / ".palace"
                / "semantic_index",
            )
            if not self._personal_semantic.load_index():
                count = self._personal_semantic.index_vault(
                    self.dual_config.personal_path
                )
                if count > 0:
                    self._personal_semantic.save_index()
        return self._personal_semantic

    # -------------------------------------------------------------------------
    # Lifecycle Hooks
    # -------------------------------------------------------------------------

    def session_start(self) -> HookResult:
        """
        Run session start hook.
        Loads ~2K tokens: vault listing, North Star, active work, palace wings, KG stats.
        Auto-populates KG from vault if empty. Builds semantic index if missing.
        """
        try:
            kg_stats = self.kg.stats()
            if kg_stats["triple_count"] == 0:
                count = self.kg.populate_from_vault(self.vault_path)
                logger.info("Auto-populated KG with %d triples on session start", count)
        except Exception as e:
            logger.warning("KG auto-population failed: %s", e)

        if self._enable_semantic:
            try:
                _ = self.semantic  # triggers lazy build
            except Exception as e:
                logger.warning("Semantic index build failed: %s", e)

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
        Validates writes, auto-adds to palace, updates KG + search index.
        """
        result = self.hooks.run_post_tool(tool_name, tool_input, self)

        if tool_name in ("write", "edit", "create_file"):
            file_path = tool_input.get("file_path") or tool_input.get("path")
            if file_path:
                path = Path(file_path)
                self._auto_add_to_palace(file_path)
                self._auto_update_kg(path)
                self._auto_update_index(path)

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
            room = path.stem.lower().replace(" ", "-")
            if "brain" in parts:
                wing = "brain"
            elif "work" in parts:
                wing = "work"
            elif "org" in parts and "people" in parts:
                wing = path.stem  # person name
                room = "context"
            else:
                return

            self.palace.create_room(wing, room)
            self.palace.link_drawer(wing, room, str(path))
        except Exception as e:
            logger.warning("Palace auto-add failed for %s: %s", file_path, e)

    def _auto_update_kg(self, path: Path) -> None:
        """Auto-update knowledge graph when a note is written/edited."""
        if path.suffix != ".md":
            return
        try:
            added = self.kg.populate_from_note(path, self.vault_path)
            if added > 0:
                logger.debug("KG updated: %d triples from %s", added, path.name)
        except Exception as e:
            logger.warning("KG auto-update failed for %s: %s", path, e)

    def _auto_update_index(self, path: Path) -> None:
        """Incrementally update semantic index when a note is written/edited."""
        if path.suffix != ".md" or not self._enable_semantic:
            return
        try:
            if self._semantic is not None:
                self._semantic.update_file(path)
                logger.debug("Search index updated for %s", path.name)
        except Exception as e:
            logger.warning("Index auto-update failed for %s: %s", path, e)

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
        wing: Optional[str] = None,
        room: Optional[str] = None,
        vaults: Optional[list[str]] = None,
    ) -> list[SearchResult]:
        """
        Search the vault(s) semantically.

        Args:
            query: Search query
            limit: Max results
            hybrid: Use hybrid (semantic + keyword) search
            wing: Filter by palace wing
            room: Filter by palace room
            vaults: Which vaults to search. Options: ["shared"], ["personal"],
                    ["shared", "personal"]. Default: ["shared"] in dual mode,
                    or the single vault in legacy mode.
        """
        if not self.is_dual_vault:
            vaults = ["shared"]  # single vault acts as shared
        elif vaults is None:
            vaults = ["shared"]

        all_results = []

        if "shared" in vaults:
            all_results.extend(
                self._search_vault(
                    self.vault, self.semantic, query, limit, hybrid, wing, room
                )
            )

        if "personal" in vaults and self.personal_vault:
            personal_results = self._search_vault(
                self.personal_vault,
                self.personal_semantic,
                query,
                limit,
                hybrid,
                wing,
                room,
            )
            # Tag personal results
            for r in personal_results:
                r.match_type = f"personal:{r.match_type}"
            all_results.extend(personal_results)

        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:limit]

    def _search_vault(
        self,
        vault: Vault,
        semantic: Optional[SemanticIndex],
        query: str,
        limit: int,
        hybrid: bool,
        wing: Optional[str] = None,
        room: Optional[str] = None,
    ) -> list[SearchResult]:
        """Search a single vault."""
        if semantic is None:
            notes = vault.search_by_name(query)
            return [
                SearchResult(
                    path=str(n.path),
                    content_excerpt=n.content[:200],
                    score=1.0,
                    match_type="name",
                )
                for n in notes[:limit]
            ]

        results = semantic.search(query, limit, hybrid)

        if wing or room:
            filtered = []
            for r in results:
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
        semantic = self.semantic  # access once to narrow Optional for mypy
        if semantic is None:
            return 0
        semantic.clear()
        count = semantic.index_vault(self.vault_path)
        semantic.save_index()
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
        """Update a brain note and sync to KG + search index."""
        self.vault.update_brain_note(note_name, content, append)

        # Sync brain note to KG and search index
        assert self.vault.config.brain_folder is not None
        brain_path = self.vault.config.brain_folder / f"{note_name}.md"
        if brain_path.exists():
            self._auto_update_kg(brain_path)
            self._auto_update_index(brain_path)
            self._auto_add_to_palace(str(brain_path))

    def get_brain_note(self, name: str) -> Optional["Note"]:
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
        valid_from: Optional[str] = None,
        source: Optional[str] = None,
    ) -> None:
        """Add a fact to the knowledge graph."""
        self.kg.add_triple(
            subject, predicate, object, valid_from=valid_from, source=source
        )

    def kg_query(self, entity: str, as_of: Optional[str] = None) -> list:
        """Query the knowledge graph."""
        return self.kg.query_entity(entity, as_of=as_of)

    def kg_timeline(self, entity: str) -> list:
        """Get entity timeline."""
        return self.kg.timeline(entity)

    def kg_populate(self) -> int:
        """Populate KG from all vault notes (wikilinks, tags, folders)."""
        return self.kg.populate_from_vault(self.vault_path)

    def sync(self) -> dict:
        """
        Full sync: rebuild KG from vault, rebuild search index, rebuild palace.

        Returns dict with counts for each system.
        """
        kg_count = self.kg.populate_from_vault(self.vault_path)
        palace_count = self.palace.auto_build_from_vault(self.vault_path)
        index_count = self.rebuild_index() if self._enable_semantic else 0

        result = {
            "kg_triples": kg_count,
            "palace_wings": palace_count,
            "indexed_files": index_count,
        }

        # Sync personal vault too if in dual mode
        if self.is_dual_vault and self.personal_kg and self.personal_palace:
            assert self.dual_config.personal_path is not None
            p_kg = self.personal_kg.populate_from_vault(self.dual_config.personal_path)
            p_palace = self.personal_palace.auto_build_from_vault(
                self.dual_config.personal_path
            )
            result["personal_kg_triples"] = p_kg
            result["personal_palace_wings"] = p_palace

        logger.info("Full sync complete: %s", result)
        return result
