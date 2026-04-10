"""
Knowledge Graph — Temporal Entity-Relationship Graph for AgnosticObsidian.
Inspired by MemPalace's knowledge_graph.py. SQLite-based triples with validity windows.

Usage:
    from ompa import KnowledgeGraph
    kg = KnowledgeGraph(db_path="./workspace/.palace/knowledge_graph.sqlite3")
    kg.add_triple("Kai", "works_on", "Orion", valid_from="2025-06-01")
    kg.query_entity("Kai")
    kg.timeline("Orion")
"""
import hashlib
import sqlite3
from dataclasses import dataclass
from datetime import datetime, date
from pathlib import Path
from typing import Optional


DEFAULT_KG_PATH = "~/.agnostic-obsidian/knowledge_graph.sqlite3"


@dataclass
class Triple:
    subject: str
    predicate: str
    object: str
    valid_from: Optional[str] = None
    valid_to: Optional[str] = None
    confidence: float = 1.0
    source_file: Optional[str] = None


class KnowledgeGraph:
    def __init__(self, db_path: str = None):
        self.db_path = Path(db_path or DEFAULT_KG_PATH).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        """Get a database connection."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Initialize the database schema."""
        conn = self._conn()
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
        """)
        conn.commit()
        conn.close()

    def _entity_id(self, name: str) -> str:
        """Generate a stable ID for an entity."""
        return hashlib.sha256(name.encode()).hexdigest()[:16]

    def _triple_id(self, subject: str, predicate: str, object: str) -> str:
        """Generate a stable ID for a triple."""
        key = f"{subject}|{predicate}|{object}"
        return hashlib.sha256(key.encode()).hexdigest()[:16]

    def _now(self) -> str:
        return datetime.now().strftime("%Y-%m-%d")

    # -------------------------------------------------------------------------
    # Entity operations
    # -------------------------------------------------------------------------

    def add_entity(self, name: str, type: str = "unknown") -> None:
        """Add an entity."""
        conn = self._conn()
        entity_id = self._entity_id(name)
        conn.execute(
            "INSERT OR IGNORE INTO entities (id, name, type) VALUES (?, ?, ?)",
            (entity_id, name, type)
        )
        conn.commit()
        conn.close()

    def query_entity(self, name: str, as_of: str = None) -> list[Triple]:
        """
        Query all triples for an entity.

        Args:
            name: Entity name
            as_of: YYYY-MM-DD date for historical query. If None, returns current facts only.
        """
        conn = self._conn()
        as_of = as_of or self._now()

        # Current facts: valid_from <= as_of AND (valid_to IS NULL OR valid_to >= as_of)
        query = """
            SELECT subject, predicate, object, valid_from, valid_to, confidence, source_file
            FROM triples
            WHERE subject = ? OR object = ?
            ORDER BY valid_from DESC
        """
        rows = conn.execute(query, (name, name)).fetchall()
        conn.close()

        triples = []
        for row in rows:
            valid_from = row["valid_from"]
            valid_to = row["valid_to"]

            # Historical filter
            if valid_from and valid_from > as_of:
                continue
            if valid_to and valid_to < as_of:
                continue

            triples.append(Triple(
                subject=row["subject"],
                predicate=row["predicate"],
                object=row["object"],
                valid_from=valid_from,
                valid_to=valid_to,
                confidence=row["confidence"],
                source_file=row["source_file"],
            ))
        return triples

    def query_relation(self, subject: str, predicate: str) -> list[Triple]:
        """Query all triples matching a specific subject+predicate."""
        conn = self._conn()
        rows = conn.execute(
            """SELECT subject, predicate, object, valid_from, valid_to, confidence, source_file
               FROM triples
               WHERE subject = ? AND predicate = ?""",
            (subject, predicate)
        ).fetchall()
        conn.close()
        return [
            Triple(
                subject=r["subject"],
                predicate=r["predicate"],
                object=r["object"],
                valid_from=r["valid_from"],
                valid_to=r["valid_to"],
                confidence=r["confidence"],
                source_file=r["source_file"],
            )
            for r in rows
        ]

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
        conn = self._conn()

        # Ensure entities exist
        self.add_entity(subject)
        self.add_entity(object)

        triple_id = self._triple_id(subject, predicate, object)
        valid_from = valid_from or self._now()

        conn.execute(
            """INSERT OR REPLACE INTO triples
               (id, subject, predicate, object, valid_from, valid_to, confidence, source_file)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (triple_id, subject, predicate, object, valid_from, valid_to, confidence, source)
        )
        conn.commit()
        conn.close()

    def invalidate(self, subject: str, predicate: str, object: str,
                   ended: str = None) -> None:
        """
        Invalidate a triple by setting its valid_to date.
        The fact is no longer current but remains queryable historically.
        """
        ended = ended or self._now()
        conn = self._conn()
        conn.execute(
            """UPDATE triples SET valid_to = ?
               WHERE subject = ? AND predicate = ? AND object = ? AND valid_to IS NULL""",
            (ended, subject, predicate, object)
        )
        conn.commit()
        conn.close()

    # -------------------------------------------------------------------------
    # Timeline
    # -------------------------------------------------------------------------

    def timeline(self, entity: str) -> list[dict]:
        """
        Get the chronological story of an entity.
        Returns all triples ordered by valid_from with direction indicators.
        """
        conn = self._conn()
        rows = conn.execute(
            """SELECT subject, predicate, object, valid_from, valid_to, source_file
               FROM triples
               WHERE subject = ? OR object = ?
               ORDER BY valid_from ASC NULLS FIRST""",
            (entity, entity)
        ).fetchall()
        conn.close()

        timeline = []
        for row in rows:
            # Determine direction and label
            if row["subject"] == entity:
                direction = "outbound"
                label = f"{entity} --{row['predicate']}--> {row['object']}"
            else:
                direction = "inbound"
                label = f"{row['subject']} --{row['predicate']}--> {entity}"

            timeline.append({
                "date": row["valid_from"],
                "end_date": row["valid_to"],
                "direction": direction,
                "subject": row["subject"],
                "predicate": row["predicate"],
                "object": row["object"],
                "label": label,
                "source": row["source_file"],
            })
        return timeline

    # -------------------------------------------------------------------------
    # Statistics
    # -------------------------------------------------------------------------

    def stats(self) -> dict:
        """Get knowledge graph statistics."""
        conn = self._conn()
        entity_count = conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        triple_count = conn.execute("SELECT COUNT(*) FROM triples").fetchone()[0]

        # Oldest and newest facts
        oldest = conn.execute(
            "SELECT MIN(valid_from) FROM triples WHERE valid_from IS NOT NULL"
        ).fetchone()[0]
        newest = conn.execute(
            "SELECT MAX(valid_from) FROM triples WHERE valid_from IS NOT NULL"
        ).fetchone()[0]

        # Count of current vs expired
        now = self._now()
        current = conn.execute(
            "SELECT COUNT(*) FROM triples WHERE (valid_to IS NULL OR valid_to >= ?)",
            (now,)
        ).fetchone()[0]

        conn.close()
        return {
            "entity_count": entity_count,
            "triple_count": triple_count,
            "current_facts": current,
            "expired_facts": triple_count - current,
            "oldest_fact": oldest,
            "newest_fact": newest,
        }
