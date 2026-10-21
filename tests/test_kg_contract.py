"""
Contract test for docs/kg-schema-v1.md.

Builds a real OMPA vault (notes -> kg_populate, plus an explicit kg_add), then checks
the SQLite file against the tables parsed out of the markdown document. If the code
or the document drifts, this fails.
"""

import hashlib
import re
import sqlite3
from pathlib import Path

import pytest

from ompa import Ompa
from ompa.kg_schema import (
    ENTITIES_COLUMNS,
    KG_RELATIVE_PATH,
    KG_SCHEMA_VERSION,
    RESERVED_PREDICATES,
    TRIPLES_COLUMNS,
    check_kg_schema,
)

DOC = Path(__file__).resolve().parent.parent / "docs" / "kg-schema-v1.md"


def _section(name: str) -> str:
    text = DOC.read_text(encoding="utf-8")
    m = re.search(rf"<!-- contract:{name} -->\n(.*?)<!-- /contract:{name} -->", text, re.S)
    assert m, f"contract:{name} block missing from {DOC.name}"
    return m.group(1)


def _table_rows(name: str) -> list[list[str]]:
    rows = []
    for line in _section(name).strip().splitlines()[2:]:  # skip header + separator
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        rows.append(cells)
    return rows


def _doc_columns(name: str) -> dict[str, tuple[str, bool]]:
    out = {}
    for cells in _table_rows(name):
        col = cells[0].strip("`")
        out[col] = (cells[1].upper(), cells[2].lower() == "yes")
    return out


def _db_columns(conn, table: str) -> dict[str, tuple[str, bool]]:
    return {
        r[1]: (str(r[2]).upper(), bool(r[3]))
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


@pytest.fixture
def vault_db(tmp_path):
    vault = tmp_path / "vault"
    (vault / "work" / "active").mkdir(parents=True)
    (vault / "work" / "active" / "auth.md").write_text(
        "---\ndate: 2025-06-01\ntags: [auth, security]\n"
        "description: Auth migration notes for the Orion project\n---\n"
        "Kai leads this. See [[Orion]] and [[Kai|our lead]].\n",
        encoding="utf-8",
    )
    ao = Ompa(vault_path=str(vault), enable_semantic=False)
    assert ao.kg_populate() > 0
    ao.kg_add("Kai", "works_on", "Orion", valid_from="2025-06-01", source="manual")
    return vault, vault / KG_RELATIVE_PATH


def test_doc_version_matches_code():
    assert _section("version").strip() == f"schema_version: {KG_SCHEMA_VERSION}"


def test_doc_tables_match_code_constants():
    assert _doc_columns("triples") == TRIPLES_COLUMNS
    assert _doc_columns("entities") == ENTITIES_COLUMNS
    assert tuple(r[0].strip("`") for r in _table_rows("predicates")) == RESERVED_PREDICATES


def test_real_vault_matches_documented_columns(vault_db):
    _, db = vault_db
    with sqlite3.connect(db) as conn:
        assert _db_columns(conn, "triples") == _doc_columns("triples")
        assert _db_columns(conn, "entities") == _doc_columns("entities")
        assert conn.execute("PRAGMA user_version").fetchone()[0] == KG_SCHEMA_VERSION
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert check_kg_schema(db) == []


def test_real_vault_matches_documented_semantics(vault_db):
    vault, db = vault_db
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        triples = conn.execute("SELECT * FROM triples").fetchall()
        entities = conn.execute("SELECT * FROM entities").fetchall()

    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    ts_re = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$")
    for t in triples:
        key = f"{t['subject']}|{t['predicate']}|{t['object']}"
        assert t["id"] == hashlib.sha256(key.encode()).hexdigest()[:16]
        assert t["valid_from"] is None or date_re.match(t["valid_from"])
        assert t["valid_to"] is None or date_re.match(t["valid_to"])
        assert 0.0 <= t["confidence"] <= 1.0
        assert ts_re.match(t["extracted_at"])
    for e in entities:
        assert e["id"] == hashlib.sha256(e["name"].encode()).hexdigest()[:16]
        assert ts_re.match(e["created_at"])

    # Every subject/object has an entity row.
    names = {e["name"] for e in entities}
    assert {t["subject"] for t in triples} | {t["object"] for t in triples} <= names

    # Reserved predicates written from the note, with documented subject/object shapes.
    facts = {(t["subject"], t["predicate"], t["object"]): t for t in triples}
    note_src = str(vault / "work" / "active" / "auth.md")
    for fact in [
        ("auth", "links_to", "Orion"),
        ("auth", "links_to", "Kai"),  # piped link: text before "|"
        ("auth", "has_tag", "auth"),
        ("auth", "in_folder", "work"),
        ("auth", "in_subfolder", "work/active"),
        ("auth", "created_on", "2025-06-01"),
    ]:
        assert fact in facts, fact
        assert facts[fact]["source_file"] == note_src
    assert facts[("auth", "created_on", "2025-06-01")]["valid_from"] == "2025-06-01"
    assert facts[("Kai", "works_on", "Orion")]["source_file"] == "manual"


def test_one_row_per_fact_and_invalidate_keeps_row(vault_db):
    vault, db = vault_db
    ao = Ompa(vault_path=str(vault), enable_semantic=False)
    ao.kg_add("Kai", "works_on", "Orion", valid_from="2025-07-01", source="again")
    ao.kg.invalidate("Kai", "works_on", "Orion", ended="2025-08-01")
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            "SELECT valid_from, valid_to, source_file FROM triples "
            "WHERE subject='Kai' AND predicate='works_on' AND object='Orion'"
        ).fetchall()
    assert rows == [("2025-07-01", "2025-08-01", "again")]


def test_legacy_unstamped_db_is_upgraded(tmp_path):
    from ompa import KnowledgeGraph

    db = tmp_path / "kg.sqlite3"
    KnowledgeGraph(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 0")
    assert check_kg_schema(db) == []  # 0 is accepted as v1
    KnowledgeGraph(str(db))
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 1


def test_future_version_is_flagged_not_downgraded(tmp_path):
    from ompa import KnowledgeGraph

    db = tmp_path / "kg.sqlite3"
    KnowledgeGraph(str(db))
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA user_version = 2")
    KnowledgeGraph(str(db))
    with sqlite3.connect(db) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert any("user_version=2" in p for p in check_kg_schema(db))


def test_checker_reports_missing_column(tmp_path):
    db = tmp_path / "bad.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE triples (id TEXT PRIMARY KEY, subject TEXT NOT NULL)")
    problems = check_kg_schema(db)
    assert "triples.source_file missing" in problems
    assert "missing table entities" in problems
