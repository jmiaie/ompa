"""
OMPA knowledge-graph interchange contract (kg-schema v1).

This module is the code side of ``docs/kg-schema-v1.md``. It holds the contract
version and the column set that other projects (locus, pliamem, AegisFlow) may
rely on when they read an OMPA ``knowledge_graph.sqlite3`` file, plus a small
checker they can call before reading.

It only imports the standard library so it can be vendored or copied as-is.

Usage:
    from ompa.kg_schema import check_kg_schema
    problems = check_kg_schema("./vault/.palace/knowledge_graph.sqlite3")
    if problems:
        raise RuntimeError("; ".join(problems))
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

#: Contract major version, stored in the database as ``PRAGMA user_version``.
#: 0 means "written by OMPA before stamping existed"; its shape is identical to v1.
KG_SCHEMA_VERSION = 1

#: Versions a v1 reader accepts.
SUPPORTED_READ_VERSIONS = (0, 1)

#: Relative location of the KG inside a vault.
KG_RELATIVE_PATH = Path(".palace") / "knowledge_graph.sqlite3"

#: Contract columns: name -> (declared SQLite type, NOT NULL).
#: Readers must select these by name and ignore any other column.
TRIPLES_COLUMNS: dict[str, tuple[str, bool]] = {
    "id": ("TEXT", False),  # PRIMARY KEY; sha256("subject|predicate|object")[:16]
    "subject": ("TEXT", True),
    "predicate": ("TEXT", True),
    "object": ("TEXT", True),
    "valid_from": ("TEXT", False),  # YYYY-MM-DD
    "valid_to": ("TEXT", False),  # YYYY-MM-DD, NULL = still valid
    "confidence": ("REAL", False),  # 0.0-1.0, default 1.0
    "source_file": ("TEXT", False),  # provenance; see doc for path rules
    "extracted_at": ("TEXT", False),  # "YYYY-MM-DD HH:MM:SS" UTC
}

ENTITIES_COLUMNS: dict[str, tuple[str, bool]] = {
    "id": ("TEXT", False),  # PRIMARY KEY; sha256(name)[:16]
    "name": ("TEXT", True),
    "type": ("TEXT", False),  # default 'unknown'
    "created_at": ("TEXT", False),  # "YYYY-MM-DD HH:MM:SS" UTC
}

#: Predicates OMPA itself writes when it populates the KG from vault notes.
RESERVED_PREDICATES = ("links_to", "has_tag", "in_folder", "in_subfolder", "created_on")


def _columns(conn: sqlite3.Connection, table: str) -> dict[str, tuple[str, bool]]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {r[1]: (str(r[2]).upper(), bool(r[3])) for r in rows}


def check_kg_schema(db_path: str | Path) -> list[str]:
    """Return a list of contract violations for the KG at ``db_path`` (empty = OK).

    Opens the database read-only and never writes to it. Extra columns or
    tables are allowed (additive changes do not break v1 readers).
    """
    db_path = Path(db_path)
    if not db_path.exists():
        return [f"{db_path} does not exist"]

    problems: list[str] = []
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version not in SUPPORTED_READ_VERSIONS:
            problems.append(
                f"user_version={version}; this reader supports {SUPPORTED_READ_VERSIONS}"
            )
        for table, expected in (("triples", TRIPLES_COLUMNS), ("entities", ENTITIES_COLUMNS)):
            actual = _columns(conn, table)
            if not actual:
                problems.append(f"missing table {table}")
                continue
            for col, (col_type, notnull) in expected.items():
                if col not in actual:
                    problems.append(f"{table}.{col} missing")
                elif actual[col] != (col_type, notnull):
                    problems.append(
                        f"{table}.{col} is {actual[col]}, expected {(col_type, notnull)}"
                    )
    finally:
        conn.close()
    return problems
