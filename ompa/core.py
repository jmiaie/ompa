"""
OMPA — Universal AI Agent Memory Layer
Core module integrating vault, palace, KG, hooks, classifier, and semantic search.
Supports single-vault (legacy) and dual-vault (shared + personal) architecture.
"""

import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from .classifier import Classification, MessageClassifier
from .config import DualVaultConfig, IsolationMode, VaultTarget
from .governance import (
    AccessPolicy,
    PointInTimeView,
    VaultGovernance,
    apply_retention_vault,
    forget_vault,
    purge_index_paths,
)
from .hooks import HookManager, HookResult
from .keyword import KeywordIndex
from .knowledge_graph import KnowledgeGraph
from .palace import Palace
from .provenance import cite
from .semantic import SearchResult, SemanticIndex, scan_vault
from .vault import Note, Vault, _safe_resolve

logger = logging.getLogger(__name__)


def _index_key(vault_path: Path, note_path: Path) -> Path:
    """Path form used as the semantic-index key (matches ``vault_path.rglob``)."""
    vault_path = Path(vault_path)
    try:
        return vault_path / Path(note_path).resolve().relative_to(vault_path.resolve())
    except ValueError:
        return Path(note_path)


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
        embedding_backend=None,  # EmbeddingBackend protocol — e.g. NIMEmbeddingBackend
        # Dual-vault parameters
        shared_vault_path: str | Path = None,
        personal_vault_path: str | Path = None,
        isolation_mode: str = "strict",
        index_refresh_interval: float = 1.0,
        # Audit memory (see docs/AUDIT.md)
        principal: str | None = None,
        audit: bool | None = None,
        audit_key=None,
    ):
        """
        Audit-memory args:
            principal: user/agent/team id this instance acts as (ACL checks,
                audit actor, fact provenance).  Default: $OMPA_PRINCIPAL, else
                ``agent_name``.  Self-asserted: see docs/AUDIT.md threat model.
            audit: True = enable the hash-chained audit log (created + baseline
                on first use); False = off; None (default) = on iff the vault
                already has ``.palace/audit.sqlite3`` or ``.palace/acl.json``.
            audit_key: signer for entries/checkpoints: a key-file path
                (Ed25519 PEM needs the ``audit`` extra; otherwise an HMAC key)
                or a signer object.  Default: $OMPA_AUDIT_KEY_FILE if set.
        """
        self.agent_name = agent_name
        self._enable_semantic = enable_semantic
        # Seconds between automatic staleness checks of the semantic index on
        # search (0 = check on every search).  Writes through Ompa.write() are
        # indexed immediately regardless; this catches edits made outside OMPA.
        self.index_refresh_interval = index_refresh_interval
        self._last_refresh: dict[tuple[int, int], float] = {}
        self._embedding_backend = embedding_backend  # optional custom backend
        self._session_started = False
        self._last_classification: Classification | None = None

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
                db_path=str(self.dual_config.shared_path / ".palace" / "knowledge_graph.sqlite3")
            )

            # Personal vault systems
            self.personal_vault = Vault(self.dual_config.personal_path)
            self.personal_palace = Palace(self.dual_config.personal_path / ".palace")
            self.personal_kg = KnowledgeGraph(
                db_path=str(self.dual_config.personal_path / ".palace" / "knowledge_graph.sqlite3")
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

        # Governance: audit chain + ACL + data policies, one context per vault
        self.principal = principal or os.environ.get("OMPA_PRINCIPAL") or agent_name
        signer = self._resolve_signer(audit_key)
        self._signer = signer
        self.gov = VaultGovernance(self.vault_path, self.principal, audit, signer, "shared")
        self.personal_gov: VaultGovernance | None = None
        if self.personal_kg is not None:
            self.personal_gov = VaultGovernance(
                self.dual_config.personal_path, self.principal, audit, signer, "personal"
            )
        for gov, kg in ((self.gov, self.kg), (self.personal_gov, self.personal_kg)):
            if gov is None:
                continue
            self._wire_kg(kg, gov)
            if gov.created:
                gov.baseline(kg)

        self.classifier = MessageClassifier()
        self.hooks = HookManager(self.vault_path, agent_name=self.agent_name)

        # Semantic search (lazy-loaded); annotated explicitly to help mypy
        self._semantic: SemanticIndex | None = None
        self._personal_semantic: SemanticIndex | None = None
        # BM25 keyword index over note content (stdlib SQLite FTS5; always on)
        self._keyword: KeywordIndex | None = None
        self._personal_keyword: KeywordIndex | None = None

    # -------------------------------------------------------------------------
    # Governance plumbing
    # -------------------------------------------------------------------------

    @staticmethod
    def _resolve_signer(audit_key):
        if audit_key is None:
            env = os.environ.get("OMPA_AUDIT_KEY_FILE")
            audit_key = env or None
        if audit_key is None:
            return None
        if isinstance(audit_key, str | Path):
            from .audit import load_key

            return load_key(audit_key)
        return audit_key

    def _wire_kg(self, kg: KnowledgeGraph, gov: VaultGovernance) -> None:
        kg.audit = gov.audit
        kg.guard = gov.kg_guard if gov.acl is not None else None
        kg.agent = self.principal

    def _ctx(self, vault: str = "shared"):
        """(gov, kg, keyword, semantic attribute name, vault path) for a vault name."""
        if vault == "personal":
            if self.personal_gov is None:
                raise ValueError("no personal vault (not in dual-vault mode)")
            return (
                self.personal_gov,
                self.personal_kg,
                self.personal_keyword,
                "_personal_semantic",
                self.dual_config.personal_path,
            )
        if vault != "shared":
            raise ValueError(f"unknown vault {vault!r}")
        return self.gov, self.kg, self.keyword, "_semantic", self.vault_path

    def _govs(self, vaults: list[str] | None = None) -> list[str]:
        names = vaults or (["shared", "personal"] if self.personal_gov else ["shared"])
        return [n for n in names if n == "shared" or self.personal_gov is not None]

    @property
    def is_dual_vault(self) -> bool:
        """True if dual-vault mode is active."""
        return self.dual_config.is_dual_vault

    @property
    def keyword(self) -> KeywordIndex:
        """Lazy-open the BM25 keyword index (kept fresh by search/write)."""
        if self._keyword is None:
            self._keyword = KeywordIndex(self.vault_path / ".palace" / "keyword_index.sqlite3")
        return self._keyword

    @property
    def personal_keyword(self) -> KeywordIndex | None:
        """Lazy-open the personal vault's keyword index (dual-vault mode only)."""
        if self._personal_keyword is None and self.dual_config.personal_path:
            self._personal_keyword = KeywordIndex(
                self.dual_config.personal_path / ".palace" / "keyword_index.sqlite3"
            )
        return self._personal_keyword

    @property
    def semantic(self) -> SemanticIndex | None:
        """Lazy-load semantic index on first access."""
        if self._semantic is None and self._enable_semantic:
            self._semantic = SemanticIndex(
                index_path=self.vault_path / ".palace" / "semantic_index",
                embedding_backend=self._embedding_backend,
                keyword_index=self.keyword,
            )
            if not self._semantic.load_index():
                count = self._semantic.index_vault(self.vault_path)
                if count > 0:
                    self._semantic.save_index()
        return self._semantic

    @property
    def personal_semantic(self) -> SemanticIndex | None:
        """Lazy-load personal semantic index."""
        if (
            self._personal_semantic is None
            and self._enable_semantic
            and self.dual_config.personal_path
        ):
            self._personal_semantic = SemanticIndex(
                index_path=self.dual_config.personal_path / ".palace" / "semantic_index",
                embedding_backend=self._embedding_backend,
                keyword_index=self.personal_keyword,
            )
            if not self._personal_semantic.load_index():
                count = self._personal_semantic.index_vault(self.dual_config.personal_path)
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
                with self.gov.txn():
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
                # Raises AccessDeniedError (logged) if this principal may not write here
                self.gov.require("write", "post_tool:" + tool_name, self.gov.rel(path))
                self._auto_add_to_palace(file_path)
                with self.gov.txn():
                    self._auto_update_kg(path)
                    if self.gov.audit is not None and path.suffix == ".md" and path.exists():
                        self.gov.log_note("note.observed", path, {"tool": tool_name})
                self._auto_update_index(path)

        return result

    def pre_compact(self, transcript: str) -> HookResult:
        """Run pre-compact hook before context compaction."""
        return self.hooks.run_pre_compact(transcript, self)

    def stop(self) -> HookResult:
        """Run stop hook (wrap-up checklist)."""
        result = self.hooks.run_stop(self)
        for sem in (self._semantic, self._personal_semantic):
            if sem is not None:
                try:
                    sem.flush()
                except Exception as e:
                    logger.debug("Semantic index flush failed: %s", e)
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
        """Incrementally update the search indexes when a note is written/edited."""
        if path.suffix != ".md":
            return
        try:
            self.keyword.update_file(_index_key(self.vault_path, path))
        except Exception as e:
            logger.debug("Keyword index update failed for %s: %s", path, e)
        if not self._enable_semantic:
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
    def last_classification(self) -> Classification | None:
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
        citations: bool = True,
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
            citations: attach a provenance ``citation`` to each result (path,
                    line/byte span, content_sha256, author, recorded_at,
                    valid_from/valid_to; see ompa.provenance).

        Raises AccessDeniedError (logged to the audit chain) if the principal
        may not read a requested vault.
        """
        # Determine which vaults to search
        if not self.is_dual_vault:
            vaults = ["shared"]  # single vault acts as shared
        elif vaults is None:
            vaults = ["shared"]
        for name in vaults:
            if name == "shared":
                self.gov.require("read", "search")
            elif name == "personal" and self.personal_gov is not None:
                self.personal_gov.require("read", "search")

        all_results = []

        # Search shared vault
        if "shared" in vaults:
            all_results.extend(
                self._search_vault(
                    self.vault, self.semantic, query, limit, hybrid, wing, room, self.keyword
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
                self.personal_keyword,
            )
            # Tag personal results
            for r in personal_results:
                r.match_type = f"personal:{r.match_type}"
            all_results.extend(personal_results)

        # Sort by score and limit
        all_results.sort(key=lambda r: r.score, reverse=True)
        results = all_results[:limit]
        if citations:
            for r in results:
                self._attach_citation(r, query)
        return results

    def _attach_citation(self, r: SearchResult, query: str) -> None:
        personal = r.match_type.startswith("personal:")
        gov = self.personal_gov if personal else self.gov
        sem = self._personal_semantic if personal else self._semantic
        recorded = None
        if gov is not None and gov.audit is not None:
            recorded = gov.audit.index_get("note", gov.rel(r.path))
        try:
            r.citation = cite(
                r.path,
                chunk_index=r.chunk_index,
                excerpt=r.content_excerpt,
                query=query,
                indexed_sig=sem.file_mtimes.get(r.path) if sem is not None else None,
                recorded=recorded,
            )
        except Exception as e:  # provenance must never break retrieval
            logger.debug("Citation failed for %s: %s", r.path, e)

    def _search_vault(
        self,
        vault: Vault,
        semantic: SemanticIndex | None,
        query: str,
        limit: int,
        hybrid: bool,
        wing: str = None,
        room: str = None,
        keyword: KeywordIndex | None = None,
    ) -> list[SearchResult]:
        """Search a single vault.

        Hybrid (semantic + BM25, RRF-fused) when the semantic extra is available,
        BM25 keyword search over note content otherwise, and note-name matching
        as a last resort (e.g. SQLite without FTS5, or no keyword hit).
        """
        self._maybe_refresh(semantic, vault.vault_path, keyword)
        results: list[SearchResult] = []
        if semantic is not None and semantic.available and semantic.chunks:
            results = semantic.search(query, limit, hybrid)
        elif keyword is not None and keyword.available:
            results = keyword.search(query, limit)

        if not results:
            notes = vault.search_by_name(query)
            results = [
                SearchResult(
                    path=str(n.path),
                    content_excerpt=n.content[:200],
                    score=1.0,
                    match_type="name",
                )
                for n in notes[:limit]
            ]

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

    def _maybe_refresh(
        self,
        semantic: SemanticIndex | None,
        vault_path: Path,
        keyword: KeywordIndex | None = None,
    ) -> None:
        """Re-index new/changed/deleted notes if the last check is old enough.

        One directory walk is shared by the semantic and keyword indexes.
        """
        now = time.monotonic()
        key = (id(semantic), id(keyword))
        last = self._last_refresh.get(key)
        if last is not None and now - last < self.index_refresh_interval:
            return
        self._last_refresh[key] = now
        try:
            snapshot = scan_vault(vault_path)
        except Exception as e:
            logger.debug("Vault scan for index refresh failed: %s", e)
            return
        for index in (keyword, semantic):
            if index is None:
                continue
            try:
                changes = index.refresh(vault_path, snapshot=snapshot)
                if any(changes.values()):
                    logger.debug("%s refreshed: %s", type(index).__name__, changes)
            except Exception as e:
                logger.debug("Index refresh skipped: %s", e)

    def qsearch(self, query: str, limit: int = 5) -> list[SearchResult]:
        """QMD-style semantic search. Convenience method."""
        return self.search(query, limit, hybrid=True)

    def rebuild_index(self) -> int:
        """Rebuild the search indexes (keyword, and semantic when enabled).

        Returns the number of notes indexed.
        """
        self.gov.require("write", "rebuild_index")
        keyword_count = self.keyword.index_vault(self.vault_path)
        semantic = self.semantic  # access property once; narrows Optional
        if semantic is None:
            return keyword_count
        semantic.clear()
        count = semantic.index_vault(self.vault_path)
        semantic.save_index()
        return count or keyword_count

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
        brain_path = self.vault.config.brain_folder / f"{note_name}.md"
        self.gov.require("write", "update_brain", f"brain/{note_name}")
        existed = brain_path.exists()
        if existed and not append:
            self.gov.check_hold_note(brain_path, "overwrite")
        with self.gov.txn():
            self.vault.update_brain_note(note_name, content, append)

            # Sync brain note to KG and search index
            if brain_path.exists():
                self._auto_update_kg(brain_path)
                self.gov.log_note("note.edit" if existed else "note.write", brain_path)
        if brain_path.exists():
            self._auto_update_index(brain_path)
            self._auto_add_to_palace(str(brain_path))

    def get_brain_note(self, name: str) -> object | None:
        """Get a brain note by name."""
        self.gov.require("read", "get_brain_note", f"brain/{name}")
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
        self.kg.add_triple(subject, predicate, object, valid_from=valid_from, source=source)

    def kg_query(self, entity: str, as_of: str = None) -> list:
        """Query the knowledge graph."""
        return self.kg.query_entity(entity, as_of=as_of)

    def kg_timeline(self, entity: str) -> list:
        """Get entity timeline."""
        return self.kg.timeline(entity)

    def kg_populate(self) -> int:
        """Populate KG from all vault notes (wikilinks, tags, folders)."""
        self.gov.require("write", "kg_populate")
        with self.gov.txn():
            return self.kg.populate_from_vault(self.vault_path)

    def sync(self) -> dict:
        """
        Full sync: rebuild KG from vault, rebuild search index, rebuild palace.

        Returns dict with counts for each system.
        """
        self.gov.require("write", "sync")
        with self.gov.txn():
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
            self.personal_gov.require("write", "sync")
            with self.personal_gov.txn():
                p_kg = self.personal_kg.populate_from_vault(self.dual_config.personal_path)
            p_palace = self.personal_palace.auto_build_from_vault(self.dual_config.personal_path)
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
            target_vault = self.vault if target == VaultTarget.SHARED else self.personal_vault
        elif self.dual_config.isolation_mode == IsolationMode.MANUAL:
            # In manual mode, default to personal (safe default)
            target = self.dual_config.default_vault
            target_vault = self.vault if target == VaultTarget.SHARED else self.personal_vault
        else:
            # Auto-classify
            target = self.dual_config.classify_content(content, tags=tags, file_path=file_path)
            target_vault = self.vault if target == VaultTarget.SHARED else self.personal_vault

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

        frontmatter: dict[str, Any] = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "tags": tags,
            "vault": target.value,
        }

        full_path = _safe_resolve(target_vault.vault_path, file_path)
        gov = self.gov if target == VaultTarget.SHARED else self.personal_gov
        # ACL + legal hold checks happen before any change (denials are logged)
        gov.require("write", "write", gov.rel(full_path))
        existed = full_path.exists()
        if existed:
            gov.check_hold_note(full_path, "overwrite")
        note = Note(path=full_path, frontmatter=frontmatter, content=content)
        target_kg = self.kg if target == VaultTarget.SHARED else self.personal_kg
        # One audit transaction: the note write and every fact derived from it
        with gov.txn():
            note.save()
            # Update KG + index
            if target_kg:
                target_kg.populate_from_note(full_path, target_vault.vault_path)
            gov.log_note("note.edit" if existed else "note.write", full_path, {"tags": tags})

        # Make the note searchable now (no manual rebuild).  Only touches an index
        # that is already loaded; an index loaded later catches up via refresh().
        target_semantic = (
            self._semantic if target == VaultTarget.SHARED else self._personal_semantic
        )
        target_keyword = self.keyword if target == VaultTarget.SHARED else self.personal_keyword
        key = _index_key(target_vault.vault_path, full_path)
        if target_keyword is not None:
            try:
                target_keyword.update_file(key)
            except Exception as e:
                logger.debug("Keyword index update after write failed for %s: %s", full_path, e)
        if target_semantic is not None:
            try:
                target_semantic.update_file(key, save=False)
            except Exception as e:
                logger.debug("Index update after write failed for %s: %s", full_path, e)

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

        # Validate paths upfront to prevent traversal
        try:
            source = _safe_resolve(self.dual_config.personal_path, note_path)
            target = _safe_resolve(self.dual_config.shared_path, note_path)
        except ValueError:
            return {"success": False, "error": f"Invalid note_path: {note_path}"}

        if self.dual_config.isolation_mode == IsolationMode.STRICT and confirm:
            # In strict mode, first call returns preview for confirmation
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
                "target": str(target),
                "sanitized": sanitize,
                "preview": content[:500],
            }

        # Perform the export
        if not source.exists():
            return {"success": False, "error": f"Note not found: {note_path}"}
        self.personal_gov.require("read", "export_to_shared", note_path)
        self.gov.require("write", "export_to_shared", note_path)
        if target.exists():
            self.gov.check_hold_note(target, "overwrite")

        note = Note.from_file(source)
        if sanitize:
            note.content = self._sanitize_content(note.content)

        # Update frontmatter for shared vault
        note.frontmatter["vault"] = "shared"
        note.frontmatter.pop("@private", None)

        note.path = target
        existed = target.exists()
        with self.gov.txn():
            note.save()

            # Update shared KG
            self.kg.populate_from_note(target, self.dual_config.shared_path)
            self.gov.log_note(
                "note.edit" if existed else "note.write", target, {"exported_from": "personal"}
            )

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

        # Validate paths upfront to prevent traversal
        try:
            source = _safe_resolve(self.dual_config.shared_path, note_path)
            target = _safe_resolve(self.dual_config.personal_path, note_path)
        except ValueError:
            return {"success": False, "error": f"Invalid note_path: {note_path}"}

        if not source.exists():
            return {"success": False, "error": f"Note not found: {note_path}"}
        self.gov.require("read", "import_to_personal", note_path)
        self.personal_gov.require("write", "import_to_personal", note_path)
        if target.exists():
            self.personal_gov.check_hold_note(target, "overwrite")

        note = Note.from_file(source)
        note.frontmatter["vault"] = "personal"
        note.frontmatter["imported_from"] = str(source)

        if link_back:
            note.content += f"\n\n---\n*Imported from shared: [[{note_path}]]*"

        note.path = target
        existed = target.exists()
        with self.personal_gov.txn():
            note.save()

            # Update personal KG
            if self.personal_kg:
                self.personal_kg.populate_from_note(target, self.dual_config.personal_path)
            self.personal_gov.log_note(
                "note.edit" if existed else "note.write", target, {"imported_from": "shared"}
            )

        logger.info("Imported %s to personal vault", note_path)
        return {
            "success": True,
            "source": str(source),
            "target": str(target),
        }

    # -------------------------------------------------------------------------
    # Audit memory: deletion, erasure, policies, ACL, verification, as_of
    # -------------------------------------------------------------------------

    def delete_note(self, file_path: str, vault: str = "shared") -> dict:
        """Delete a note (and the facts derived from it), recorded in the audit chain.

        Blocked (LegalHoldError) if the note is under legal hold.
        """
        gov, kg, keyword, sem_attr, root = self._ctx(vault)
        full = _safe_resolve(root, file_path)
        gov.require("write", "delete_note", gov.rel(full))
        if not full.exists():
            return {"success": False, "error": f"Note not found: {file_path}"}
        gov.check_hold_note(full, "delete")
        target = str(full.resolve())
        with gov.txn():
            full.unlink()
            ids = []
            if kg is not None:
                with kg._conn() as conn:
                    rows = conn.execute(
                        "SELECT id, source_file FROM triples WHERE source_file IS NOT NULL"
                    ).fetchall()
                ids = [r[0] for r in rows if str(Path(r[1]).resolve()) == target]
            n = kg.delete_ids(ids) if kg is not None else 0
            if n:
                gov.log("fact.delete", {"ids": ids, "source": gov.rel(full), "count": n})
            gov.log_note("note.delete", full)
        sem = getattr(self, sem_attr)
        purge_index_paths(keyword, sem, {target})
        if sem is not None:
            sem.save_index()
        return {"success": True, "path": str(full), "facts_deleted": n}

    def forget(
        self, subject: str, vaults: list[str] | None = None, dry_run: bool = False
    ) -> list[dict]:
        """Right to erasure: remove every trace of ``subject`` from the vault(s).

        Deletes notes named ``subject``; redacts whole-word, case-insensitive
        mentions in other notes, palace metadata and session logs; renames
        paths containing it; deletes matching KG facts/entities; drops the
        affected search-index entries; redacts matching audit-entry bodies
        while keeping their hashes, and appends a ``forget`` tombstone (proof
        of deletion without the data); then VACUUMs every SQLite file.
        Requires ``admin``; blocked by a covering legal hold.

        Returns one receipt per vault.  ``nonce`` in a receipt is stored
        nowhere else: ``sha256(nonce + "\\0" + subject.casefold())`` equals
        the tombstone's ``subject_commitment``.
        """
        out = []
        for name in self._govs(vaults):
            gov, kg, keyword, sem_attr, _root = self._ctx(name)
            out.append(forget_vault(self, gov, kg, keyword, sem_attr, subject, dry_run))
        return out

    def apply_retention(
        self, vaults: list[str] | None = None, dry_run: bool = False, now=None
    ) -> list[dict]:
        """Delete notes/facts older than their retention rule (skipping legal holds)."""
        out = []
        for name in self._govs(vaults):
            gov, kg, keyword, sem_attr, _root = self._ctx(name)
            out.append(apply_retention_vault(self, gov, kg, keyword, sem_attr, now, dry_run))
        return out

    def _policy_change(self, gov: VaultGovernance, op: str, item: dict) -> None:
        gov.require("admin", op)
        gov.policies.save()
        gov.log("policy.change", {"op": op, "item": item})

    def add_retention_rule(self, ttl_days: float, vault: str = "shared", **scope) -> dict:
        """Add a retention rule: ``tag=``, ``folder=``, ``predicate=`` or ``all=True``."""
        gov = self._ctx(vault)[0]
        gov.require("admin", "retention.add")
        rule = gov.policies.add_retention(ttl_days, **scope)
        self._policy_change(gov, "retention.add", rule)
        return rule

    def add_legal_hold(
        self, value: str, kind: str = "subject", reason: str = "", vault: str = "shared"
    ) -> dict:
        """Place a legal hold (``kind`` = subject | path | tag); blocks deletion."""
        gov = self._ctx(vault)[0]
        gov.require("admin", "hold.add")
        hold = gov.policies.add_hold(kind, value, reason)
        self._policy_change(gov, "hold.add", hold)
        return hold

    def remove_policy(self, policy_id: str, vault: str = "shared") -> dict | None:
        """Remove a retention rule or release a legal hold by id."""
        gov = self._ctx(vault)[0]
        gov.require("admin", "policy.remove")
        item = gov.policies.remove(policy_id)
        if item is not None:
            self._policy_change(gov, "policy.remove", item)
        return item

    def policies(self, vault: str = "shared") -> dict:
        gov = self._ctx(vault)[0]
        gov.require("read", "policies")
        return gov.policies.data

    def set_acl(self, acl: dict, vault: str = "shared") -> dict:
        """Install/replace a vault's ACL (``admin`` required once one exists)."""
        gov, kg, *_ = self._ctx(vault)
        gov.require("admin", "acl.set")
        policy = AccessPolicy(acl, gov.palace / "acl.json")
        if gov.audit is None:  # ACL implies audit (denials must be recorded)
            gov.audit = VaultGovernance(
                gov.vault_path, gov.principal, True, self._signer, gov.name
            ).audit
            gov.baseline(kg)
        policy.save()
        gov.acl = policy
        gov.log("acl.change", {"acl": acl})
        self._wire_kg(kg, gov)
        return acl

    def enable_audit(self, vault: str = "shared", baseline: bool = True) -> dict:
        """Turn on the audit chain for an existing vault (migration).  Idempotent."""
        gov, kg, *_ = self._ctx(vault)
        gov.require("admin", "audit.enable")
        if gov.audit is not None:
            return {"enabled": True, "created": False, "entries": len(gov.audit)}
        gov.audit = VaultGovernance(
            gov.vault_path, gov.principal, True, self._signer, gov.name
        ).audit
        self._wire_kg(kg, gov)
        counts = gov.baseline(kg) if baseline else {}
        return {"enabled": True, "created": True, "baseline": counts, "head": gov.audit.head()}

    def audit_verify(
        self, vaults: list[str] | None = None, verifier=None, anchors=None, drift: bool = True
    ) -> dict:
        """Verify each vault's audit chain (+ note drift); ``{"ok": bool, vault: report}``."""
        out: dict = {"ok": True}
        for name in self._govs(vaults):
            gov = self._ctx(name)[0]
            gov.require("read", "audit.verify")
            if gov.audit is None:
                out[name] = {"enabled": False}
                continue
            rep = gov.audit.verify(verifier=verifier, anchors=anchors).to_dict()
            if drift:
                rep["drift"] = gov.drift()
            out[name] = rep
            out["ok"] = out["ok"] and rep["ok"]
        return out

    def audit_head(self, vault: str = "shared") -> dict:
        """Latest chain head -- store it outside the vault to anchor the log."""
        gov = self._ctx(vault)[0]
        gov.require("read", "audit.head")
        return gov.audit.head() if gov.audit is not None else {}

    def audit_export(
        self, vault: str = "shared", start: int = 1, end: int | None = None, proofs_for=None
    ) -> dict:
        """Evidence bundle (JSON-serialisable) for entries ``start..end``."""
        gov = self._ctx(vault)[0]
        gov.require("read", "audit.export")
        if gov.audit is None:
            raise RuntimeError("audit is not enabled for this vault")
        bundle = gov.audit.export_bundle(start, end, proofs_for)
        gov.log("audit.export", {"start": start, "end": end, "entries": len(bundle["entries"])})
        return bundle

    def as_of(self, t, vault: str = "shared") -> PointInTimeView:
        """What did the agent know at transaction time ``t``?  (replays the chain)"""
        gov = self._ctx(vault)[0]
        gov.require("read", "as_of")
        if gov.audit is None:
            raise RuntimeError("as_of() needs the audit log (Ompa(audit=True))")
        return PointInTimeView(gov.audit, t)

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
                    tags=[str(t) for t in (note.frontmatter.get("tags") or [])],  # type: ignore[attr-defined]
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
