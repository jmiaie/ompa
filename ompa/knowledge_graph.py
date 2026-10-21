"""
Knowledge Graph — Temporal Entity-Relationship Graph for OMPA.
Inspired by MemPalace's knowledge_graph.py. SQLite-based triples with validity windows.

WAL mode is enabled by default for concurrent read performance. Composite indexes
on (subject, valid_from) and (object, predicate) accelerate timeline and reverse
lookups on large vaults.

Usage:
    from ompa import KnowledgeGraph
    kg = KnowledgeGraph(db_path="./workspace/.palace/knowledge_graph.sqlite3")
    kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
    kg.query_entity("Kai")
    kg.timeline("Orion")
"""

import hashlib
import logging
import re
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

DEFAULT_KG_PATH = "~/.ompa/knowledge_graph.sqlite3"


@dataclass
class Triple:
    subject: str
    predicate: str
    object: str
    valid_from: str | None = None
    valid_to: str | None = None
    confidence: float = 1.0
    source_file: str | None = None
    # Provenance (audit-memory).  ``recorded_at`` is transaction time (when the
    # fact was written); valid_from/valid_to are valid time.
    recorded_at: str | None = None
    agent: str | None = None
    source_line: int | None = None
    source_sha256: str | None = None

    @property
    def citation(self) -> dict:
        """Where this fact came from: note path, line, content hash, author, times."""
        return {
            "path": self.source_file,
            "line": self.source_line,
            "content_sha256": self.source_sha256,
            "author": self.agent,
            "recorded_at": self.recorded_at,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }


# Column list used by every read query (keeps Triple construction uniform).
_TRIPLE_COLS = (
    "subject, predicate, object, valid_from, valid_to, confidence, source_file, "
    "extracted_at, agent, source_line, source_sha256"
)


def _row_to_triple(row: sqlite3.Row) -> Triple:
    """Convert a SQLite Row to a Triple dataclass."""
    keys = row.keys()
    return Triple(
        subject=row["subject"],
        predicate=row["predicate"],
        object=row["object"],
        valid_from=row["valid_from"],
        valid_to=row["valid_to"],
        confidence=row["confidence"],
        source_file=row["source_file"],
        recorded_at=row["extracted_at"] if "extracted_at" in keys else None,
        agent=row["agent"] if "agent" in keys else None,
        source_line=row["source_line"] if "source_line" in keys else None,
        source_sha256=row["source_sha256"] if "source_sha256" in keys else None,
    )


class AccessDeniedError(PermissionError):
    """Raised when an access-control guard rejects an operation."""


@lru_cache(maxsize=65536)
def _entity_id(name: str) -> str:
    """Stable entity id (pure function of the name; cached, it's on the write path)."""
    return hashlib.sha256(name.encode()).hexdigest()[:16]


class KnowledgeGraph:
    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or DEFAULT_KG_PATH).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()  # per-thread connection cache
        # Optional governance hooks, wired by Ompa when audit/ACL are enabled:
        #   audit: ompa.audit.AuditLog -- every mutation is appended to the chain
        #   guard: callable(op: "read"|"write") raising AccessDeniedError
        #   agent: author/agent id stamped on new facts (provenance)
        self.audit = None
        self.guard = None
        self.agent: str | None = None
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        """Return a thread-local connection, creating one if needed."""
        if not getattr(self._local, "conn", None):
            conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            # WAL mode: readers don't block writers; writers don't block readers.
            # Persists in the db file — only needed once, idempotent thereafter.
            conn.execute("PRAGMA journal_mode=WAL")
            # NORMAL is safe with WAL and significantly faster than FULL.
            conn.execute("PRAGMA synchronous=NORMAL")
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def _conn(self):
        """Yield the thread-local connection for a single transaction."""
        conn = self._get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_db(self) -> None:
        """Initialize the database schema and indexes."""
        with self._conn() as conn:
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

                -- Basic lookup indexes
                CREATE INDEX IF NOT EXISTS idx_triples_subject   ON triples(subject);
                CREATE INDEX IF NOT EXISTS idx_triples_predicate ON triples(predicate);
                CREATE INDEX IF NOT EXISTS idx_triples_object    ON triples(object);

                -- Composite indexes for timeline queries and reverse lookups
                CREATE INDEX IF NOT EXISTS idx_triples_subject_date
                    ON triples(subject, valid_from);
                CREATE INDEX IF NOT EXISTS idx_triples_object_pred
                    ON triples(object, predicate);
                CREATE INDEX IF NOT EXISTS idx_triples_validity
                    ON triples(valid_from, valid_to);

                -- Exact-fact lookup for add_triple's overlap check.  Without it
                -- each write scans every row sharing the subject or the
                -- (object, predicate) pair, i.e. O(fan-out) per insert.
                CREATE INDEX IF NOT EXISTS idx_triples_spo
                    ON triples(subject, predicate, object);
            """)
            # Provenance columns (added in place on pre-existing databases).
            have = {r[1] for r in conn.execute("PRAGMA table_info(triples)")}
            for col, decl in (
                ("agent", "TEXT"),
                ("source_line", "INTEGER"),
                ("source_sha256", "TEXT"),
            ):
                if col not in have:
                    conn.execute(f"ALTER TABLE triples ADD COLUMN {col} {decl}")  # nosec B608

    def _entity_id(self, name: str) -> str:
        """Generate a stable ID for an entity."""
        return _entity_id(name)

    def _interval_id(self, subject: str, predicate: str, obj: str, valid_from: str | None) -> str:
        """Stable ID for one validity interval of a fact."""
        key = f"{subject}|{predicate}|{obj}|{valid_from or ''}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    def _check(self, op: str) -> None:
        if self.guard is not None:
            self.guard(op)

    def _log(self, action: str, body: dict) -> None:
        if self.audit is not None:
            self.audit.append(action, body, actor=self.agent)

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

    def query_entity(self, name: str, as_of: str = None) -> list[Triple]:
        """
        Query all current triples for an entity.

        Args:
            name: Entity name
            as_of: YYYY-MM-DD date for historical query. Defaults to today.
        """
        as_of = as_of or self._now()

        # Filter in SQL for performance
        query = (
            f"SELECT {_TRIPLE_COLS} FROM triples "  # nosec B608 - constant column list
            "WHERE (subject = ? OR object = ?)"
            " AND (valid_from IS NULL OR valid_from <= ?)"
            " AND (valid_to IS NULL OR valid_to >= ?)"
            " ORDER BY valid_from DESC"
        )
        self._check("read")
        with self._conn() as conn:
            rows = conn.execute(query, (name, name, as_of, as_of)).fetchall()

        return [_row_to_triple(row) for row in rows]

    def query_relation(self, subject: str, predicate: str) -> list[Triple]:
        """Query all triples matching a specific subject+predicate."""
        self._check("read")
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT {_TRIPLE_COLS} FROM triples WHERE subject = ? AND predicate = ?",  # nosec B608
                (subject, predicate),
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
        valid_from: str = None,
        valid_to: str = None,
        confidence: float = 1.0,
        source: str = None,
        source_line: int | None = None,
        source_sha256: str | None = None,
        agent: str | None = None,
        recorded_at: str | None = None,
    ) -> str:
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
            source_line: 1-based line in ``source`` the fact was extracted from.
            source_sha256: SHA-256 of the source content at extraction time.
            agent: Author/agent id (defaults to ``self.agent``).
            recorded_at: Override the recorded (transaction) time; used by
                point-in-time replay.  Defaults to now.

        Returns ``"add"`` (new interval) or ``"merge"`` (merged into an
        overlapping interval).  With auditing enabled the mutation is appended
        to the audit chain as ``fact.add`` / ``fact.merge``.
        """
        self._check("write")
        valid_from = valid_from or self._now()
        agent = agent or self.agent
        subject_id = self._entity_id(subject)
        object_id = self._entity_id(object)
        interval_id = self._interval_id(subject, predicate, object, valid_from)

        with self._conn() as conn:
            # The first statement is a write, so this (deferred) transaction holds
            # SQLite's write lock from here to commit: the check-then-write below
            # is serialized across threads and processes without BEGIN IMMEDIATE.
            conn.execute(
                "INSERT OR IGNORE INTO entities (id, name, type) VALUES (?, ?, ?), (?, ?, ?)",
                (subject_id, subject, "unknown", object_id, object, "unknown"),
            )
            # Fast path (the common case): no overlapping interval for this exact
            # fact -> append a new row.  Indexed point lookup, no merge logic.
            overlap = conn.execute(
                """SELECT 1 FROM triples
                   WHERE subject = ? AND predicate = ? AND object = ?
                     AND (valid_to IS NULL OR valid_to >= ?)
                     AND (? IS NULL OR valid_from IS NULL OR valid_from <= ?)
                   LIMIT 1""",
                (subject, predicate, object, valid_from, valid_to, valid_to),
            ).fetchone()
            if overlap is None:
                conn.execute(
                    """INSERT OR REPLACE INTO triples
                       (id, subject, predicate, object, valid_from, valid_to, confidence,
                        source_file, agent, source_line, source_sha256, extracted_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                               COALESCE(?, CURRENT_TIMESTAMP))""",
                    (
                        interval_id,
                        subject,
                        predicate,
                        object,
                        valid_from,
                        valid_to,
                        confidence,
                        source,
                        agent,
                        source_line,
                        source_sha256,
                        recorded_at,
                    ),
                )
                outcome = "add"
            else:
                # Slow path: overlaps an existing interval (re-assertion) -> merge.
                self._upsert_interval(
                    conn, subject, predicate, object, valid_from, valid_to, confidence, source
                )
                if source_line is not None or source_sha256 is not None or agent:
                    conn.execute(
                        """UPDATE triples SET agent = COALESCE(?, agent),
                               source_line = COALESCE(?, source_line),
                               source_sha256 = COALESCE(?, source_sha256)
                           WHERE subject = ? AND predicate = ? AND object = ?
                             AND (valid_to IS NULL OR valid_to >= ?)
                             AND (valid_from IS NULL OR valid_from <= COALESCE(?, valid_from))""",
                        (
                            agent,
                            source_line,
                            source_sha256,
                            subject,
                            predicate,
                            object,
                            valid_from,
                            valid_to,
                        ),
                    )
                outcome = "merge"
        if self.audit is not None:
            self._log(
                "fact." + outcome,
                {
                    "s": subject,
                    "p": predicate,
                    "o": object,
                    "valid_from": valid_from,
                    "valid_to": valid_to,
                    "confidence": confidence,
                    "source": source,
                    "source_line": source_line,
                    "source_sha256": source_sha256,
                },
            )
        return outcome

    def _upsert_interval(
        self,
        conn: sqlite3.Connection,
        subject: str,
        predicate: str,
        obj: str,
        valid_from: str,
        valid_to: str | None,
        confidence: float,
        source: str | None,
    ) -> None:
        """Record that (subject, predicate, obj) holds over [valid_from, valid_to].

        Validity history is append-only: each (subject, predicate, object) can
        have many rows, one per validity interval.  Re-asserting a fact that was
        invalidated earlier opens a *new* interval instead of overwriting the old
        one, so point-in-time queries for the earlier period stay correct.
        An assertion that overlaps existing interval(s) for the same fact is
        merged with them (interval union), which keeps repeated assertions of a
        still-true fact idempotent.
        """
        # Inclusive overlap test; NULL valid_to means "still true" (+infinity)
        rows = conn.execute(
            """SELECT id, valid_from, valid_to FROM triples
               WHERE subject = ? AND predicate = ? AND object = ?
                 AND (valid_to IS NULL OR valid_to >= ?)
                 AND (? IS NULL OR valid_from IS NULL OR valid_from <= ?)
               ORDER BY valid_from""",
            (subject, predicate, obj, valid_from, valid_to, valid_to),
        ).fetchall()

        if not rows:
            conn.execute(
                """INSERT OR REPLACE INTO triples
                   (id, subject, predicate, object, valid_from, valid_to, confidence, source_file)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._interval_id(subject, predicate, obj, valid_from),
                    subject,
                    predicate,
                    obj,
                    valid_from,
                    valid_to,
                    confidence,
                    source,
                ),
            )
            return

        starts = [valid_from] + [r["valid_from"] for r in rows]
        merged_from = None if any(v is None for v in starts) else min(starts)
        ends = [valid_to] + [r["valid_to"] for r in rows]
        merged_to = None if any(v is None for v in ends) else max(ends)

        keep_id = rows[0]["id"]
        for r in rows[1:]:
            conn.execute("DELETE FROM triples WHERE id = ?", (r["id"],))
        conn.execute(
            """UPDATE triples
               SET valid_from = ?, valid_to = ?, confidence = ?,
                   source_file = COALESCE(?, source_file)
               WHERE id = ?""",
            (merged_from, merged_to, confidence, source, keep_id),
        )

    def invalidate(self, subject: str, predicate: str, obj: str, ended: str = None) -> int:
        """
        Invalidate a triple by setting its valid_to date.
        The fact is no longer current but remains queryable historically.
        Returns the number of open intervals closed.
        """
        self._check("write")
        ended = ended or self._now()
        with self._conn() as conn:
            n = conn.execute(
                """UPDATE triples SET valid_to = ?
                   WHERE subject = ? AND predicate = ? AND object = ? AND valid_to IS NULL""",
                (ended, subject, predicate, obj),
            ).rowcount
        if self.audit is not None:
            self._log(
                "fact.invalidate",
                {"s": subject, "p": predicate, "o": obj, "ended": ended, "rows": n},
            )
        return n

    # -------------------------------------------------------------------------
    # Deletion (used by retention / erasure / note deletion; callers audit)
    # -------------------------------------------------------------------------

    def find_ids(self, where: str, args: tuple = ()) -> list[str]:
        """Ids of triples matching a trusted SQL ``where`` fragment (internal use)."""
        with self._conn() as conn:
            return [
                r[0]
                for r in conn.execute(f"SELECT id FROM triples WHERE {where}", args)  # nosec B608
            ]

    def delete_ids(self, ids: list[str]) -> int:
        """Hard-delete triples by id.  Caller is responsible for auditing."""
        if not ids:
            return 0
        n = 0
        with self._conn() as conn:
            for i in range(0, len(ids), 500):
                chunk = ids[i : i + 500]
                marks = ",".join("?" * len(chunk))
                n += conn.execute(
                    f"DELETE FROM triples WHERE id IN ({marks})",  # nosec B608
                    chunk,
                ).rowcount
        return n

    def delete_entities(self, names: list[str]) -> int:
        """Remove entity rows (no longer referenced by any triple)."""
        n = 0
        with self._conn() as conn:
            for name in names:
                n += conn.execute(
                    """DELETE FROM entities WHERE name = ? AND NOT EXISTS
                       (SELECT 1 FROM triples WHERE subject = ? OR object = ?)""",
                    (name, name, name),
                ).rowcount
        return n

    def compact(self) -> None:
        """VACUUM + truncate the WAL so deleted rows leave no recoverable bytes."""
        conn = self._get_connection()
        conn.commit()
        conn.execute("PRAGMA secure_delete=ON")
        conn.execute("VACUUM")
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def close(self) -> None:
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            conn.close()
            self._local.conn = None

    # -------------------------------------------------------------------------
    # Timeline
    # -------------------------------------------------------------------------

    def timeline(self, entity: str) -> list[dict]:
        """
        Get the chronological story of an entity.
        Returns all triples ordered by valid_from with direction indicators.
        """
        self._check("read")
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT subject, predicate, object, valid_from, valid_to, source_file,
                          source_line, source_sha256, agent, extracted_at
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
                    "citation": {
                        "path": row["source_file"],
                        "line": row["source_line"],
                        "content_sha256": row["source_sha256"],
                        "author": row["agent"],
                        "recorded_at": row["extracted_at"],
                        "valid_from": row["valid_from"],
                        "valid_to": row["valid_to"],
                    },
                }
            )
        return timeline

    # -------------------------------------------------------------------------
    # Auto-population from vault
    # -------------------------------------------------------------------------

    def populate_from_note(self, note_path: Path, vault_path: Path = None) -> int:
        """
        Extract and store triples from a single vault note.

        Extracts:
        - Wikilinks: note --links_to--> target
        - Frontmatter tags: note --has_tag--> tag
        - Folder membership: note --in_folder--> folder_name
        - Frontmatter date: note --created_on--> date

        Returns the number of triples added.
        """
        if not note_path.exists() or note_path.suffix != ".md":
            return 0

        count = 0
        note_name = note_path.stem
        source = str(note_path)

        try:
            raw_bytes = note_path.read_bytes()
        except OSError as e:
            logger.debug("Could not read %s: %s", note_path, e)
            return 0
        sha = hashlib.sha256(raw_bytes).hexdigest()
        raw = raw_bytes.decode("utf-8", errors="replace")
        try:
            import frontmatter as fm

            post = fm.loads(raw)
            content = post.content
            metadata = dict(post.metadata)
        except Exception:
            content = raw
            metadata = {}
        body_off = max(raw.find(content), 0) if content else 0

        def line_at(offset: int) -> int:
            return raw.count("\n", 0, offset) + 1

        def fm_line(key: str) -> int | None:
            m = re.search(rf"^{key}\s*:", raw[:body_off], re.MULTILINE)
            return line_at(m.start()) if m else None

        prov = {"source": source, "source_sha256": sha}

        # 1. Wikilinks → links_to triples
        for m in re.finditer(r"\[\[([^\]]+)\]\]", content):
            # Strip display text from piped links: [[target|display]]
            target = m.group(1).split("|")[0].strip()
            if target:
                self.add_triple(
                    note_name,
                    "links_to",
                    target,
                    source_line=line_at(body_off + m.start()),
                    **prov,
                )
                count += 1

        # 2. Frontmatter tags → has_tag triples
        tags = metadata.get("tags", [])
        if isinstance(tags, str):
            tags = [t.strip() for t in tags.split(",") if t.strip()]
        if isinstance(tags, list):
            tags_line = fm_line("tags")
            for tag in tags:
                if isinstance(tag, str) and tag.strip():
                    self.add_triple(
                        note_name, "has_tag", tag.strip(), source_line=tags_line, **prov
                    )
                    count += 1

        # 3. Folder membership
        if vault_path:
            try:
                rel = note_path.relative_to(vault_path)
                parts = rel.parts
                if len(parts) > 1:
                    folder = parts[0]  # top-level: brain, work, org, perf
                    self.add_triple(note_name, "in_folder", folder, **prov)
                    count += 1
                    # Sub-folder (e.g., work/active, org/people)
                    if len(parts) > 2:
                        subfolder = f"{parts[0]}/{parts[1]}"
                        self.add_triple(note_name, "in_subfolder", subfolder, **prov)
                        count += 1
            except ValueError:
                pass

        # 4. Frontmatter date → created_on
        date_val = metadata.get("date")
        if date_val:
            date_str = str(date_val)[:10]  # YYYY-MM-DD
            self.add_triple(
                note_name,
                "created_on",
                date_str,
                valid_from=date_str,
                source_line=fm_line("date"),
                **prov,
            )
            count += 1

        # 5. Frontmatter description → has_description (for search context)
        desc = metadata.get("description")
        if desc and isinstance(desc, str) and len(desc) > 10:
            self.add_entity(note_name, entity_type="note")
            count += 1

        return count

    def populate_from_vault(self, vault_path: Path, exclude_patterns: list = None) -> int:
        """
        Scan all vault notes and populate the knowledge graph.

        Args:
            vault_path: Root path of the vault
            exclude_patterns: Folder/path patterns to skip

        Returns:
            Total number of triples added.
        """
        from .vault import DEFAULT_EXCLUDE_PATTERNS

        exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS
        total = 0
        vault_path = Path(vault_path)

        for md_file in vault_path.rglob("*.md"):
            if any(excl in str(md_file) for excl in exclude_patterns):
                continue
            added = self.populate_from_note(md_file, vault_path)
            total += added

        logger.info("KG populated: %d triples from vault %s", total, vault_path)
        return total

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def stats(self) -> dict:
        """Get knowledge graph statistics."""
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
