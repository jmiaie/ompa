"""
Knowledge Graph — Temporal Entity-Relationship Graph for OMPA.
Inspired by MemPalace's knowledge_graph.py. SQLite-based triples with validity windows.

Usage:
    from ompa import KnowledgeGraph
    kg = KnowledgeGraph(db_path="./workspace/.palace/knowledge_graph.sqlite3")
    kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
    kg.query_entity("Kai")
    kg.timeline("Orion")
"""

import hashlib
import logging
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .vault import (
    DEFAULT_EXCLUDE_PATTERNS,
    _fast_parse_frontmatter,
    extract_wikilinks,
)
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_KG_PATH = "~/.ompa/knowledge_graph.sqlite3"

# Predicates whose triples are derived from the on-disk content of a note
# (wikilinks, frontmatter, folder position). When the note is re-scanned
# during populate, existing triples matching these predicates AND the same
# source_file are deleted before inserting the fresh set — otherwise stale
# `links_to`, `has_tag`, etc. triples would accumulate after edits.
_NOTE_DERIVED_PREDICATES = (
    "links_to",
    "has_tag",
    "in_folder",
    "in_subfolder",
    "created_on",
)


@dataclass
class Triple:
    subject: str
    predicate: str
    object: str
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    confidence: float = 1.0
    source_file: Optional[str] = None


def _row_to_triple(row: sqlite3.Row) -> Triple:
    """Convert a SQLite Row to a Triple dataclass."""
    return Triple(
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        confidence=row["confidence"],
        source_file=row["source_file"],
    )


class KnowledgeGraph:
    def __init__(self, db_path: str | None = None):
        self.db_path = Path(db_path or DEFAULT_KG_PATH).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        """Get a database connection that auto-closes on exit."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        # journal_mode=WAL is sticky at the DB level (set once in _init_db);
        # setting PRAGMAs on every connection was itself a per-query overhead
        # that dominated short reads like query_entity/timeline.
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        """Initialize the database schema."""
        with self._conn() as conn:
            # Set sticky DB-level pragmas once. WAL + NORMAL-synchronous cut
            # fsync cost on bulk populate passes; persists across connections
            # so we don't re-pay the pragma parse on every query.
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT DEFAULT 'unknown',
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS triples (
                    id TEXT PRIMARY KEY,
                    subject TEXT NOT NULL,
                    predicate TEXT NOT NULL,
                    object TEXT NOT NULL,
                    valid_from TEXT,
                    valid_to TEXT,
                    confidence REAL DEFAULT 1.0,
                    source_file TEXT,
                    extracted_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_triples_subject ON triples(subject);
                CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate);
                CREATE INDEX IF NOT EXISTS idx_triples_object ON triples(object);
                -- Composite index for query_relation (subject + predicate
                -- filter). Closes the 8× gap vs query_entity.
                CREATE INDEX IF NOT EXISTS idx_triples_subj_pred
                    ON triples(subject, predicate);
                CREATE INDEX IF NOT EXISTS idx_triples_pred_obj
                    ON triples(predicate, object);
                CREATE INDEX IF NOT EXISTS idx_triples_source
                    ON triples(source_file);
            """)

    def _entity_id(self, name: str) -> str:
        """Generate a stable ID for an entity."""
        return hashlib.sha256(name.encode()).hexdigest()[:16]

    def _triple_id(self, subject: str, predicate: str, obj: str) -> str:
        """Generate a stable ID for a triple."""
        key = f"{subject}|{predicate}|{obj}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    # -------------------------------------------------------------------------
    # Entity operations
    # -------------------------------------------------------------------------

    def add_entity(self, name: str, entity_type: str = "unknown") -> None:
        """Add an entity."""
        entity_id = self._entity_id(name)
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO entities (id, name, type) VALUES (?, ?, ?)",
                (entity_id, name, entity_type),
            )

    def query_entity(self, name: str, as_of: str | None = None) -> list[Triple]:
        """
        Query all current triples for an entity.

        Args:
            name: Entity name
            as_of: YYYY-MM-DD date for historical query. Defaults to today.
        """
        as_of = as_of or self._now()

        # Filter in SQL for performance
        query = """
            SELECT subject, predicate, object, valid_from, valid_to, confidence, source_file
            FROM triples
            WHERE (subject = ? OR object = ?)
              AND (valid_from IS NULL OR valid_from <= ?)
              AND (valid_to IS NULL OR valid_to >= ?)
            ORDER BY valid_from DESC
        """
        with self._conn() as conn:
            rows = conn.execute(query, (name, name, as_of, as_of)).fetchall()

        return [_row_to_triple(row) for row in rows]

    def query_relation(
        self, subject: str, predicate: str, as_of: str | None = None
    ) -> list[Triple]:
        """
        Query triples matching a specific subject+predicate, filtered by validity.

        Args:
            subject: Subject entity name.
            predicate: Predicate to match.
            as_of: YYYY-MM-DD date for historical query. Defaults to today.
                Triples whose ``valid_to`` is before ``as_of`` (i.e.,
                invalidated) are excluded, consistent with ``query_entity``.
        """
        as_of = as_of or self._now()

        with self._conn() as conn:
            rows = conn.execute(
                """SELECT subject, predicate, object, valid_from, valid_to, confidence, source_file
                   FROM triples
                   WHERE subject = ? AND predicate = ?
                     AND (valid_from IS NULL OR valid_from <= ?)
                     AND (valid_to IS NULL OR valid_to >= ?)
                   ORDER BY valid_from DESC""",
                (subject, predicate, as_of, as_of),
            ).fetchall()
        return [_row_to_triple(r) for r in rows]

    # -------------------------------------------------------------------------
    # Triple operations
    # -------------------------------------------------------------------------

    def add_triple(
        self,
        subject: str,
        predicate: str,
        object: str,
        valid_from: str | None = None,
        valid_to: str | None = None,
        confidence: float = 1.0,
        source: str | None = None,
    ) -> None:
        """
        Add a fact triple to the knowledge graph.

        Args:
            subject: Subject entity (e.g., "Kai")
            predicate: Relationship (e.g., "works_on", "completed", "recommended")
            object: Object entity (e.g., "Orion")
            valid_from: Start date (YYYY-MM-DD). None = always true.
            valid_to: End date (YYYY-MM-DD). None = still true.
            confidence: Confidence 0-1. Default 1.0.
            source: Source file or drawer reference.
        """
        triple_id = self._triple_id(subject, predicate, object)
        valid_from = valid_from or self._now()
        subject_id = self._entity_id(subject)
        object_id = self._entity_id(object)

        with self._conn() as conn:
            # Ensure entities exist (single transaction)
            conn.execute(
                "INSERT OR IGNORE INTO entities (id, name, type) VALUES (?, ?, ?)",
                (subject_id, subject, "unknown"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO entities (id, name, type) VALUES (?, ?, ?)",
                (object_id, object, "unknown"),
            )
            conn.execute(
                """INSERT OR REPLACE INTO triples
                   (id, subject, predicate, object, valid_from, valid_to, confidence, source_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    triple_id,
                    subject,
                    predicate,
                    object,
                    valid_from,
                    valid_to,
                    confidence,
                    source,
                ),
            )

    def invalidate(
        self, subject: str, predicate: str, obj: str, ended: str | None = None
    ) -> None:
        """
        Invalidate a triple by setting its valid_to date.
        The fact is no longer current but remains queryable historically.
        """
        ended = ended or self._now()
        with self._conn() as conn:
            conn.execute(
                """UPDATE triples SET valid_to = ?
                   WHERE subject = ? AND predicate = ? AND object = ? AND valid_to IS NULL""",
                (ended, subject, predicate, obj),
            )

    # -------------------------------------------------------------------------
    # Timeline
    # -------------------------------------------------------------------------

    def timeline(self, entity: str) -> list[dict]:
        """
        Get the chronological story of an entity.
        Returns all triples ordered by valid_from with direction indicators.
        """
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT subject, predicate, object, valid_from, valid_to, source_file
                   FROM triples
                   WHERE subject = ? OR object = ?
                   ORDER BY valid_from ASC NULLS FIRST""",
                (entity, entity),
            ).fetchall()

        timeline = []
        for row in rows:
            # Determine direction and label
            if row["subject"] == entity:
                direction = "outbound"
                label = f"{entity} --{row['predicate']}--> {row['object']}"
            else:
                direction = "inbound"
                label = f"{row['subject']} --{row['predicate']}--> {entity}"

            timeline.append(
                {
                    "date": row["valid_from"],
                    "end_date": row["valid_to"],
                    "direction": direction,
                    "subject": row["subject"],
                    "predicate": row["predicate"],
                    "object": row["object"],
                    "label": label,
                    "source": row["source_file"],
                }
            )
        return timeline

    # -------------------------------------------------------------------------
    # Auto-population from vault
    # -------------------------------------------------------------------------

    def _extract_note_triples(
        self, note_path: Path, vault_path: Optional[Path] = None
    ) -> tuple[list[tuple], bool]:
        """Parse a note from disk and return (triples, has_description).

        Thin wrapper around :meth:`_extract_triples_from_parsed` that handles
        the disk read + frontmatter parse. Callers that already have parsed
        content (e.g. a :class:`~ompa.vault.Note`) should call the parsed-form
        helper directly to avoid a redundant parse.
        """
        if not note_path.exists() or note_path.suffix != ".md":
            return [], False

        try:
            text = note_path.read_text(encoding="utf-8")
        except Exception as e:
            logger.debug("Could not read %s: %s", note_path, e)
            return [], False

        metadata, content = _fast_parse_frontmatter(text)

        return self._extract_triples_from_parsed(
            note_path, content, metadata, vault_path=vault_path
        )

    def _extract_triples_from_parsed(
        self,
        note_path: Path,
        content: str,
        metadata: dict,
        vault_path: Optional[Path] = None,
        links: Optional[list[str]] = None,
    ) -> tuple[list[tuple], bool]:
        """Derive triples from already-parsed note content/metadata.

        Shared by the single-note and bulk population paths. ``links`` may be
        passed in to skip wikilink re-extraction when the caller already has
        them (e.g. ``Note.links``).
        """
        note_name = note_path.stem
        source = str(note_path)
        triples: list[tuple] = []

        # 1. Wikilinks → links_to triples (shared extractor with the vault
        # layer so link-parsing semantics — display-text stripping, .md
        # suffix handling — stay identical everywhere).
        if links is None:
            links = extract_wikilinks(content)
        for target in links:
            triples.append((note_name, "links_to", target, None, source))

        # 2. Frontmatter tags → has_tag triples
        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        if isinstance(tags, list):
            for tag in tags:
                if isinstance(tag, str) and tag.strip():
                    triples.append((note_name, "has_tag", tag.strip(), None, source))

        # 3. Folder membership
        if vault_path:
            try:
                rel = note_path.relative_to(vault_path)
                parts = rel.parts
                if len(parts) > 1:
                    folder = parts[0]
                    triples.append((note_name, "in_folder", folder, None, source))
                    if len(parts) > 2:
                        subfolder = f"{parts[0]}/{parts[1]}"
                        triples.append(
                            (note_name, "in_subfolder", subfolder, None, source)
                        )
            except ValueError:
                pass

        # 4. Frontmatter date → created_on
        date_val = metadata.get("date")
        if date_val:
            date_str = str(date_val)[:10]
            triples.append((note_name, "created_on", date_str, date_str, source))

        # 5. Frontmatter description → note-typed entity
        desc = metadata.get("description")
        has_description = bool(desc and isinstance(desc, str) and len(desc) > 10)

        return triples, has_description

    def _bulk_upsert_triples(
        self, conn: sqlite3.Connection, triples: list[tuple]
    ) -> None:
        """Bulk-upsert the given (subject, predicate, object, valid_from, source)
        tuples plus their subject/object entities. Uses ``executemany`` so the
        SQLite engine only parses each statement once.
        """
        if not triples:
            return

        entity_rows: dict[str, tuple[str, str, str]] = {}
        triple_rows: list[tuple] = []
        now = self._now()
        for subject, predicate, obj, valid_from, source in triples:
            sid = self._entity_id(subject)
            oid = self._entity_id(obj)
            entity_rows.setdefault(sid, (sid, subject, "unknown"))
            entity_rows.setdefault(oid, (oid, obj, "unknown"))
            triple_rows.append(
                (
                    self._triple_id(subject, predicate, obj),
                    subject,
                    predicate,
                    obj,
                    valid_from or now,
                    None,  # valid_to
                    1.0,  # confidence
                    source,
                )
            )

        conn.executemany(
            "INSERT OR IGNORE INTO entities (id, name, type) VALUES (?, ?, ?)",
            list(entity_rows.values()),
        )
        conn.executemany(
            """INSERT OR REPLACE INTO triples
               (id, subject, predicate, object, valid_from, valid_to,
                confidence, source_file)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            triple_rows,
        )

    def _delete_stale_note_triples(
        self, conn: sqlite3.Connection, source_files: list[str]
    ) -> None:
        """Delete note-derived triples (wikilinks, tags, folders, dates) for
        the given source files. Called before re-populating so stale edges
        don't accumulate when notes are edited."""
        if not source_files:
            return
        pred_placeholders = ",".join("?" * len(_NOTE_DERIVED_PREDICATES))
        # Chunk to stay under SQLite's default 999-variable limit — vaults
        # with thousands of notes would otherwise blow past it.
        CHUNK = 400
        for i in range(0, len(source_files), CHUNK):
            batch = source_files[i : i + CHUNK]
            src_placeholders = ",".join("?" * len(batch))
            # placeholders above are literal "?" characters whose count
            # depends on the batch size; all user data is bound via parameters
            # and predicates come from the hardcoded _NOTE_DERIVED_PREDICATES
            # tuple — no untrusted input is ever interpolated into the SQL.
            conn.execute(
                f"DELETE FROM triples WHERE source_file IN ({src_placeholders}) "  # nosec B608
                f"AND predicate IN ({pred_placeholders})",
                (*batch, *_NOTE_DERIVED_PREDICATES),
            )

    def populate_from_note(
        self, note_path: Path, vault_path: Path | None = None
    ) -> int:
        """
        Extract and store triples from a single vault note.

        Extracts:
        - Wikilinks: note --links_to--> target
        - Frontmatter tags: note --has_tag--> tag
        - Folder membership: note --in_folder--> folder_name
        - Frontmatter date: note --created_on--> date

        Before inserting, deletes any existing note-derived triples with the
        same source_file so stale links (from before an edit removed them)
        don't linger.

        Returns the number of triples added.
        """
        triples, has_description = self._extract_note_triples(note_path, vault_path)
        if not triples and not has_description:
            return 0

        source = str(note_path)
        note_name = note_path.stem
        with self._conn() as conn:
            self._delete_stale_note_triples(conn, [source])
            self._bulk_upsert_triples(conn, triples)
            if has_description:
                conn.execute(
                    "INSERT OR IGNORE INTO entities (id, name, type) VALUES (?, ?, ?)",
                    (self._entity_id(note_name), note_name, "note"),
                )

        return len(triples) + (1 if has_description else 0)

    def populate_from_vault(
        self,
        vault_path: Path,
        exclude_patterns: list[str] | None = None,
        vault: Optional["object"] = None,
    ) -> int:
        """
        Scan all vault notes and populate the knowledge graph in a single
        transaction. Stale triples for each re-scanned note are cleaned up
        before re-inserting the fresh set.

        Args:
            vault_path: Root path of the vault
            exclude_patterns: Folder/path patterns to skip

        Returns:
            Total number of triples added.
        """
        exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS
        vault_path = Path(vault_path)

        all_triples: list[tuple] = []
        description_notes: list[str] = []  # note stems with valid descriptions
        source_files: list[str] = []

        # Reuse the caller's Vault cache when supplied — list_notes()
        # returns pre-parsed Note objects (frontmatter + wikilinks already
        # extracted), so the KG population pass no longer re-parses every
        # file. Only fall back to a fresh Vault when called standalone.
        if vault is None:
            from .vault import Vault

            vault = Vault(vault_path)
        for note in vault.list_notes(exclude_patterns=exclude_patterns):
            triples, has_description = self._extract_triples_from_parsed(
                note.path,
                note.content,
                note.frontmatter,
                vault_path=vault_path,
                links=note.links,
            )
            if not triples and not has_description:
                continue
            all_triples.extend(triples)
            source_files.append(str(note.path))
            if has_description:
                description_notes.append(note.path.stem)

        total = len(all_triples) + len(description_notes)
        if total == 0:
            logger.info("KG populated: 0 triples from vault %s", vault_path)
            return 0

        # Single transaction: stale cleanup + bulk upsert + description entities.
        with self._conn() as conn:
            self._delete_stale_note_triples(conn, source_files)
            self._bulk_upsert_triples(conn, all_triples)
            if description_notes:
                conn.executemany(
                    "INSERT OR IGNORE INTO entities (id, name, type) VALUES (?, ?, ?)",
                    [
                        (self._entity_id(name), name, "note")
                        for name in description_notes
                    ],
                )

        logger.info("KG populated: %d triples from vault %s", total, vault_path)
        return total

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def stats(self) -> dict:
        """Get knowledge graph statistics.

        Returns:
            dict with keys:
                entity_count (int): Distinct entities in the graph.
                triple_count (int): Total triples (including expired).
                current_facts (int): Triples valid as of now.
                expired_facts (int): Triples whose ``valid_to`` is in the past.
                oldest_fact (str | None): Earliest ``valid_from`` date.
                newest_fact (str | None): Latest ``valid_from`` date.
        """
        with self._conn() as conn:
            entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
            triple_count = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]

            oldest = conn.execute(
                "SELECT MIN(valid_from) FROM triples WHERE valid_from IS NOT NULL"
            ).fetchone()[0]
            newest = conn.execute(
                "SELECT MAX(valid_from) FROM triples WHERE valid_from IS NOT NULL"
            ).fetchone()[0]

            now = self._now()
            current = conn.execute(
                "SELECT COUNT(*) FROM triples WHERE (valid_to IS NULL OR valid_to >= ?)",
                (now,),
            ).fetchone()[0]

        return {
            "entity_count": entity_count,
            "triple_count": triple_count,
            "current_facts": current,
            "expired_facts": triple_count - current,
            "oldest_fact": oldest,
            "newest_fact": newest,
        }
