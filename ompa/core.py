"""
OMPA — Universal AI Agent Memory Layer
Core module integrating vault, palace, KG, hooks, classifier, and semantic search.
Supports single-vault (legacy) and dual-vault (shared + personal) architecture.
"""

import logging
import re
import shutil
from pathlib import Path
from typing import Optional

from .vault import Vault, Note
from .palace import Palace
from .knowledge_graph import KnowledgeGraph
from .hooks import HookManager, HookResult
from .classifier import MessageClassifier, Classification
from .semantic import SemanticIndex, SearchResult
from .config import DualVaultConfig, IsolationMode, VaultTarget

logger = logging.getLogger(__name__)


class Ompa:
    """
    Universal agent memory layer.

    Integrates:
    - Vault (obsidian-mind conventions: brain/work/org/perf folders, wikilinks)
    - Palace (MemPalace wings/rooms/closets/drawers metadata layer)
    - Knowledge Graph (temporal SQLite triples with validity windows)
    - Hooks (session_start, user_message, post_tool, pre_compact, stop)
    - Classifier (15 message types with routing hints)
    - Semantic Search (local sentence-transformers)

    Usage (single vault — legacy):
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
        vault_path: str | Path = None,
        agent_name: str = "agent",
        enable_semantic: bool = True,
        # Dual-vault parameters
        shared_vault_path: str | Path = None,
        personal_vault_path: str | Path = None,
        isolation_mode: str = "strict",
    ):
        self.agent_name = agent_name
        self._enable_semantic = enable_semantic
        self._session_started = False
        self._last_classification: Optional[Classification] = None

        # Dual-vault config
        self.dual_config = DualVaultConfig(
            isolation_mode=IsolationMode(isolation_mode),
        )

        if shared_vault_path and personal_vault_path:
            # Dual-vault mode
            self.dual_config.shared_path = Path(shared_vault_path).expanduser()
            self.dual_config.personal_path = Path(personal_vault_path).expanduser()
            self.vault_path = self.dual_config.shared_path  # primary for hooks

            # Shared vault systems
            self.vault = Vault(self.dual_config.shared_path)
            self.palace = Palace(self.dual_config.shared_path / ".palace")
            self.kg = KnowledgeGraph(
                db_path=str(
                    self.dual_config.shared_path / ".palace" / "knowledge_graph.sqlite3"
                )
            )

            # Personal vault systems
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
            # Single-vault mode (legacy / backward compatible)
            self.vault_path = Path(vault_path or ".")
            self.vault = Vault(self.vault_path)
            self.palace = Palace(self.vault_path / ".palace")
            self.kg = KnowledgeGraph(
                db_path=str(self.vault_path / ".palace" / "knowledge_graph.sqlite3")
            )
            self.personal_vault = None
            self.personal_palace = None
            self.personal_kg = None

        self.classifier = MessageClassifier()
        self.hooks = HookManager(self.vault_path, agent_name=self.agent_name)

        # Semantic search (lazy-loaded)
        self._semantic = None
        self._personal_semantic = None

    @property
    def is_dual_vault(self) -> bool:
        """True if dual-vault mode is active."""
        return self.dual_config.is_dual_vault

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

    @property
    def personal_semantic(self) -> Optional[SemanticIndex]:
        """Lazy-load personal semantic index."""
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
        # Auto-populate KG if empty
        try:
            kg_stats = self.kg.stats()
            if kg_stats["triple_count"] == 0:
                count = self.kg.populate_from_vault(self.vault_path)
                logger.info("Auto-populated KG with %d triples on session start", count)
        except Exception as e:
            logger.debug("KG auto-population skipped: %s", e)

        # Trigger semantic index build if needed (lazy property handles this)
        if self._enable_semantic:
            try:
                _ = self.semantic  # triggers lazy build
            except Exception as e:
                logger.debug("Semantic index build skipped: %s", e)

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

        # Auto-update on file writes
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
        except Exception as e:
            logger.debug("Palace auto-add failed for %s: %s", file_path, e)

    def _auto_update_kg(self, path: Path) -> None:
        """Auto-update knowledge graph when a note is written/edited."""
        if path.suffix != ".md":
            return
        try:
            added = self.kg.populate_from_note(path, self.vault_path)
            if added > 0:
                logger.debug("KG updated: %d triples from %s", added, path.name)
        except Exception as e:
            logger.debug("KG auto-update failed for %s: %s", path, e)

    def _auto_update_index(self, path: Path) -> None:
        """Incrementally update semantic index when a note is written/edited."""
        if path.suffix != ".md" or not self._enable_semantic:
            return
        try:
            if self._semantic is not None:
                self._semantic.update_file(path)
                logger.debug("Search index updated for %s", path.name)
        except Exception as e:
            logger.debug("Index auto-update failed for %s: %s", path, e)

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
        vaults: list[str] = None,
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
        # Determine which vaults to search
        if not self.is_dual_vault:
            vaults = ["shared"]  # single vault acts as shared
        elif vaults is None:
            vaults = ["shared"]

        all_results = []

        # Search shared vault
        if "shared" in vaults:
            all_results.extend(
                self._search_vault(
                    self.vault, self.semantic, query, limit, hybrid, wing, room
                )
            )

        # Search personal vault
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

        # Sort by score and limit
        all_results.sort(key=lambda r: r.score, reverse=True)
        return all_results[:limit]

    def _search_vault(
        self,
        vault: Vault,
        semantic: Optional[SemanticIndex],
        query: str,
        limit: int,
        hybrid: bool,
        wing: str = None,
        room: str = None,
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
        """Update a brain note and sync to KG + search index."""
        self.vault.update_brain_note(note_name, content, append)

        # Sync brain note to KG and search index
        brain_path = self.vault.config.brain_folder / f"{note_name}.md"
        if brain_path.exists():
            self._auto_update_kg(brain_path)
            self._auto_update_index(brain_path)
            self._auto_add_to_palace(str(brain_path))

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
        if self.is_dual_vault:
            p_kg = self.personal_kg.populate_from_vault(self.dual_config.personal_path)
            p_palace = self.personal_palace.auto_build_from_vault(
                self.dual_config.personal_path
            )
            result["personal_kg_triples"] = p_kg
            result["personal_palace_wings"] = p_palace

        logger.info("Full sync complete: %s", result)
        return result

    # -------------------------------------------------------------------------
    # Dual-vault operations
    # -------------------------------------------------------------------------

    def write(
        self,
        content: str,
        file_path: str = None,
        tags: list[str] = None,
        vault: str = None,
    ) -> dict:
        """
        Write content to the appropriate vault.

        In dual-vault mode, auto-classifies content unless vault is specified.
        In single-vault mode, writes to the single vault.

        Args:
            content: Note content to write
            file_path: Target file path (relative to vault root)
            tags: Tags for classification and frontmatter
            vault: Force target vault: "shared" or "personal"

        Returns:
            dict with {vault, path, classified_as}
        """
        tags = tags or []

        # Determine target vault
        if not self.is_dual_vault:
            target = VaultTarget.SHARED
            target_vault = self.vault
        elif vault:
            target = VaultTarget(vault)
            target_vault = (
                self.vault if target == VaultTarget.SHARED else self.personal_vault
            )
        elif self.dual_config.isolation_mode == IsolationMode.MANUAL:
            # In manual mode, default to personal (safe default)
            target = self.dual_config.default_vault
            target_vault = (
                self.vault if target == VaultTarget.SHARED else self.personal_vault
            )
        else:
            # Auto-classify
            target = self.dual_config.classify_content(
                content, tags=tags, file_path=file_path
            )
            target_vault = (
                self.vault if target == VaultTarget.SHARED else self.personal_vault
            )

        # Build file path if not provided
        if not file_path:
            # Use classifier to determine folder
            classification = self.classifier.classify(content[:200])
            folder = classification.suggested_folder
            # Sanitize content for filename
            words = re.sub(r"[^\w\s]", "", content[:40]).split()
            name = "-".join(words[:5]) if words else "note"
            file_path = f"{folder}{name}.md"

        # Write the note
        from datetime import datetime

        frontmatter = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "tags": tags,
            "vault": target.value,
        }

        full_path = target_vault.vault_path / file_path
        note = Note(path=full_path, frontmatter=frontmatter, content=content)
        note.save()

        # Update KG + index
        target_kg = self.kg if target == VaultTarget.SHARED else self.personal_kg
        if target_kg:
            target_kg.populate_from_note(full_path, target_vault.vault_path)

        return {
            "vault": target.value,
            "path": str(full_path),
            "classified_as": target.value,
        }

    def export_to_shared(
        self,
        note_path: str,
        confirm: bool = True,
        sanitize: bool = True,
    ) -> dict:
        """
        Export a note from personal vault to shared vault.

        Args:
            note_path: Path relative to personal vault root
            confirm: If True, returns preview without writing (for confirmation)
            sanitize: Strip personal markers (@private, credentials, etc.)

        Returns:
            dict with {success, source, target, sanitized, preview}
        """
        if not self.is_dual_vault:
            return {"success": False, "error": "Not in dual-vault mode"}

        if self.dual_config.isolation_mode == IsolationMode.STRICT and confirm:
            # In strict mode, first call returns preview for confirmation
            source = self.dual_config.personal_path / note_path
            if not source.exists():
                return {"success": False, "error": f"Note not found: {note_path}"}

            note = Note.from_file(source)
            content = note.content

            if sanitize:
                content = self._sanitize_content(content)

            return {
                "success": True,
                "action": "preview",
                "source": str(source),
                "target": str(self.dual_config.shared_path / note_path),
                "sanitized": sanitize,
                "preview": content[:500],
            }

        # Perform the export
        source = self.dual_config.personal_path / note_path
        if not source.exists():
            return {"success": False, "error": f"Note not found: {note_path}"}

        note = Note.from_file(source)
        if sanitize:
            note.content = self._sanitize_content(note.content)

        # Update frontmatter for shared vault
        note.frontmatter["vault"] = "shared"
        note.frontmatter.pop("@private", None)

        target = self.dual_config.shared_path / note_path
        note.path = target
        note.save()

        # Update shared KG
        self.kg.populate_from_note(target, self.dual_config.shared_path)

        logger.info("Exported %s to shared vault", note_path)
        return {
            "success": True,
            "action": "exported",
            "source": str(source),
            "target": str(target),
        }

    def import_to_personal(
        self,
        note_path: str,
        link_back: bool = True,
    ) -> dict:
        """
        Import a note from shared vault to personal vault.

        Args:
            note_path: Path relative to shared vault root
            link_back: Maintain a wikilink reference to the shared original

        Returns:
            dict with {success, source, target}
        """
        if not self.is_dual_vault:
            return {"success": False, "error": "Not in dual-vault mode"}

        source = self.dual_config.shared_path / note_path
        if not source.exists():
            return {"success": False, "error": f"Note not found: {note_path}"}

        note = Note.from_file(source)
        note.frontmatter["vault"] = "personal"
        note.frontmatter["imported_from"] = str(source)

        if link_back:
            note.content += f"\n\n---\n*Imported from shared: [[{note_path}]]*"

        target = self.dual_config.personal_path / note_path
        note.path = target
        note.save()

        # Update personal KG
        if self.personal_kg:
            self.personal_kg.populate_from_note(target, self.dual_config.personal_path)

        logger.info("Imported %s to personal vault", note_path)
        return {
            "success": True,
            "source": str(source),
            "target": str(target),
        }

    def _sanitize_content(self, content: str) -> str:
        """Remove sensitive markers and credentials from content."""
        # Remove personal tags
        content = re.sub(r"@private\b", "", content)
        content = re.sub(r"#personal\b", "", content)

        # Redact credential-like patterns
        content = re.sub(r"(sk-[a-zA-Z0-9]{20,})", "[REDACTED]", content)
        content = re.sub(r"(AKIA[A-Z0-9]{16})", "[REDACTED]", content)
        content = re.sub(
            r"(token|password|secret|api_key|api-key)\s*[:=]\s*\S+",
            r"\1: [REDACTED]",
            content,
            flags=re.IGNORECASE,
        )

        return content

    def migrate_to_dual_vault(
        self,
        shared_path: str | Path,
        personal_path: str | Path,
        classification_rules: str = "auto",
    ) -> dict:
        """
        Migrate a single-vault OMPA to dual-vault architecture.

        Args:
            shared_path: Path for the shared vault
            personal_path: Path for the personal vault
            classification_rules: "auto" to auto-classify, "all-shared" to keep all in shared

        Returns:
            dict with migration stats
        """
        shared_path = Path(shared_path).expanduser()
        personal_path = Path(personal_path).expanduser()

        shared_path.mkdir(parents=True, exist_ok=True)
        personal_path.mkdir(parents=True, exist_ok=True)

        shared_count = 0
        personal_count = 0

        notes = self.vault.list_notes()
        for note in notes:
            if classification_rules == "auto":
                target = self.dual_config.classify_content(
                    note.content,
                    tags=list(note.frontmatter.get("tags", [])),
                    file_path=str(note.path),
                )
            else:
                target = VaultTarget.SHARED

            try:
                rel_path = note.path.relative_to(self.vault_path)
            except ValueError:
                continue

            if target == VaultTarget.PERSONAL:
                dest = personal_path / rel_path
                personal_count += 1
            else:
                dest = shared_path / rel_path
                shared_count += 1

            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(note.path, dest)

        # Save config
        self.dual_config.shared_path = shared_path
        self.dual_config.personal_path = personal_path
        config_path = Path("~/.ompa/config.yaml").expanduser()
        self.dual_config.to_yaml(config_path)

        return {
            "shared_notes": shared_count,
            "personal_notes": personal_count,
            "config_saved": str(config_path),
        }
