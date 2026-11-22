"""
Keyword (BM25) search over note content, backed by the SQLite FTS5 extension
that ships with Python's standard ``sqlite3`` module.

This is OMPA's default search when the ``semantic`` extra is not installed, and
the keyword half of hybrid search when it is.  It needs no extra dependencies.

The index lives in ``.palace/keyword_index.sqlite3`` and is kept fresh the same
way as the semantic index: each note's ``[st_mtime_ns, st_size]`` is recorded
when it is indexed, and :meth:`KeywordIndex.refresh` re-indexes only new or
changed notes and drops deleted ones.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_MAX_QUERY_TOKENS = 64

# Rank and excerpt in one statement: the right choice while notes are short.
_ONE_QUERY_SQL = """SELECT path, bm25(notes_fts) AS s, snippet(notes_fts, 1, '', '', '...', 48)
                    FROM notes_fts WHERE notes_fts MATCH ?
                    ORDER BY s, rowid LIMIT ?"""
_RANK_SQL = """SELECT rowid, bm25(notes_fts) AS s
               FROM notes_fts WHERE notes_fts MATCH ?
               ORDER BY s, rowid LIMIT ?"""
# The snippet of one matching note, reached by a seek to its rowid.
_EXCERPT_SQL = """SELECT path, snippet(notes_fts, 1, '', '', '...', 48)
                  FROM notes_fts WHERE notes_fts MATCH ? AND rowid = ?"""
# Index bytes per note from which notes count as long (about 300 words a note).
# See KeywordIndex.search for what changes.
_LONG_NOTE_BYTES = 4096
# Excerpts are fetched by seeking while there are at most this many of them, or
# one per this many notes in the index if that is more (see KeywordIndex._excerpts).
_SEEK_AT_MOST = 5
_NOTES_PER_SEEK = 1500


def fts5_available() -> bool:
    """True if this Python's sqlite3 was built with the FTS5 extension."""
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
            return True
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def _fts_query(query: str) -> str:
    """Turn free text into a safe FTS5 query: every word quoted, OR-ed together.

    Quoting each token neutralises FTS5 syntax (``AND``, ``NEAR``, ``*``, ``:``,
    unbalanced quotes ...), so arbitrary user text can never raise a syntax error.
    BM25 then ranks documents matching more (and rarer) words higher.
    """
    seen: list[str] = []
    for tok in _TOKEN_RE.findall(query.lower()):
        if tok not in seen:
            seen.append(tok)
        if len(seen) >= _MAX_QUERY_TOKENS:
            break
    return " OR ".join(f'"{t}"' for t in seen)


class KeywordIndex:
    """BM25 full-text index over a vault's markdown notes (one row per note)."""

    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.available = fts5_available()
        self._conn: sqlite3.Connection | None = None
        if self.available:
            self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False, timeout=30)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(
                """
                CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                    path UNINDEXED, body, tokenize = 'porter unicode61'
                );
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY, mtime_ns INTEGER NOT NULL, size INTEGER NOT NULL,
                    fts_rowid INTEGER NOT NULL
                );
                """
            )
            self._conn.commit()
        else:
            logger.warning("SQLite FTS5 unavailable; keyword search falls back to substring scan")

    # ------------------------------------------------------------------ writes

    def _write_note(self, conn: sqlite3.Connection, path: Path) -> bool:
        path_str = str(path)
        try:
            st = path.stat()
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            logger.debug("Keyword index skipping %s: %s", path, e)
            return False
        self._delete_note(conn, path_str)
        cur = conn.execute("INSERT INTO notes_fts (path, body) VALUES (?, ?)", (path_str, body))
        conn.execute(
            "INSERT INTO files (path, mtime_ns, size, fts_rowid) VALUES (?, ?, ?, ?)",
            (path_str, st.st_mtime_ns, st.st_size, cur.lastrowid),
        )
        return True

    @staticmethod
    def _delete_note(conn: sqlite3.Connection, path_str: str) -> bool:
        """Delete one note by FTS rowid (``path`` is UNINDEXED: a WHERE on it scans)."""
        row = conn.execute("SELECT fts_rowid FROM files WHERE path = ?", (path_str,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM notes_fts WHERE rowid = ?", (row[0],))
        conn.execute("DELETE FROM files WHERE path = ?", (path_str,))
        return True

    def update_file(self, path: Path | str) -> bool:
        """(Re-)index one note immediately.  Returns True on success."""
        path = Path(path)
        if self._conn is None or path.suffix != ".md" or not path.exists():
            return False
        with self._lock:
            ok = self._write_note(self._conn, path)
            self._conn.commit()
        return ok

    def remove_file(self, path: Path | str) -> bool:
        if self._conn is None:
            return False
        path_str = str(Path(path))
        with self._lock:
            removed = self._delete_note(self._conn, path_str)
            self._conn.commit()
        return removed

    def refresh(
        self,
        vault_path: Path | str,
        exclude_patterns: list | None = None,
        snapshot: dict[str, list[int]] | None = None,
    ) -> dict:
        """Index new/changed notes and drop deleted ones; ``{"added","updated","removed"}``.

        ``snapshot`` is an optional precomputed :func:`ompa.semantic.scan_vault` result.
        """
        stats = {"added": 0, "updated": 0, "removed": 0}
        if self._conn is None:
            return stats
        vault_path = Path(vault_path)
        if snapshot is None:
            from .semantic import scan_vault

            snapshot = scan_vault(vault_path, exclude_patterns)
        with self._lock:
            conn = self._conn
            known = {
                p: [m, s] for p, m, s in conn.execute("SELECT path, mtime_ns, size FROM files")
            }
            for path_str, sig in snapshot.items():
                prev = known.get(path_str)
                if prev == list(sig):
                    continue
                if self._write_note(conn, Path(path_str)):
                    stats["updated" if prev is not None else "added"] += 1
            prefix = str(vault_path)
            for path_str in known:
                if path_str.startswith(prefix) and path_str not in snapshot:
                    self._delete_note(conn, path_str)
                    stats["removed"] += 1
            conn.commit()
        return stats

    def index_vault(self, vault_path: Path | str, exclude_patterns: list | None = None) -> int:
        """Full (re)build of the index for a vault.  Returns the number of notes indexed."""
        self.clear()
        return self.refresh(vault_path, exclude_patterns)["added"]

    def clear(self) -> None:
        if self._conn is None:
            return
        with self._lock:
            self._conn.execute("DELETE FROM notes_fts")
            self._conn.execute("DELETE FROM files")
            self._conn.commit()

    def compact(self) -> None:
        """Merge FTS segments, VACUUM and truncate the WAL so deleted text is unrecoverable."""
        if self._conn is None:
            return
        with self._lock:
            self._conn.execute("INSERT INTO notes_fts(notes_fts) VALUES('optimize')")
            self._conn.commit()
            self._conn.execute("PRAGMA secure_delete=ON")
            self._conn.execute("VACUUM")
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    def __len__(self) -> int:
        if self._conn is None:
            return 0
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    # ------------------------------------------------------------------ reads

    def search(self, query: str, limit: int = 5, excerpts: bool = True):
        """BM25-ranked notes matching any word of ``query`` (best first).

        Returns :class:`ompa.semantic.SearchResult` objects with
        ``match_type="keyword"`` and ``score`` = -bm25 (higher is better).

        A snippet re-reads its whole note, and a statement that ranks and
        excerpts at once builds one for every note that is in the top ``limit``
        at any point of the scan, not only for the final results.  With short
        notes that is cheap.  With long ones it was most of the search: 4 ms of a
        4.8 ms search for 5 of 50 notes of 1,700 words.  So when the index holds
        long notes, they are ranked first and excerpts are built afterwards for
        the results only.  The ranking is the same either way.

        Pass ``excerpts=False`` to get the ranking alone (``content_excerpt`` is
        then empty) and call :meth:`fill_excerpts` for the results that are kept.
        """
        from .semantic import SearchResult

        limit = max(0, min(int(limit), 100))
        fts_q = _fts_query(query)
        if self._conn is None or not fts_q or limit == 0:
            return []
        with self._lock:
            conn = self._conn
            try:
                if excerpts and not self._long_notes(conn):
                    rows = conn.execute(_ONE_QUERY_SQL, (fts_q, limit)).fetchall()
                else:
                    ranked = conn.execute(_RANK_SQL, (fts_q, limit)).fetchall()
                    rowids = [rowid for rowid, _ in ranked]
                    found = (
                        self._excerpts(conn, fts_q, rowids)
                        if excerpts
                        else self._paths(conn, rowids)
                    )
                    rows = [
                        (found[rowid][0], s, found[rowid][1])
                        for rowid, s in ranked
                        if rowid in found
                    ]
            except sqlite3.Error as e:
                logger.warning("Keyword search failed: %s", e)
                return []
        return [
            SearchResult(path=p, content_excerpt=snip, score=-float(s), match_type="keyword")
            for p, s, snip in rows
        ]

    @staticmethod
    def _long_notes(conn: sqlite3.Connection) -> bool:
        """True if the index averages more than ``_LONG_NOTE_BYTES`` per note."""
        notes = conn.execute("SELECT MAX(id) FROM notes_fts_docsize").fetchone()[0] or 0
        if not notes:
            return False
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        page_size = conn.execute("PRAGMA page_size").fetchone()[0]
        return pages * page_size >= notes * _LONG_NOTE_BYTES

    def fill_excerpts(self, query: str, results: list) -> None:
        """Set ``content_excerpt`` on the *results* that :meth:`search` left without one."""
        fts_q = _fts_query(query)
        wanted = [r for r in results if not r.content_excerpt]
        if self._conn is None or not fts_q or not wanted:
            return
        with self._lock:
            try:
                by_rowid = {}
                for result in wanted:
                    row = self._conn.execute(
                        "SELECT fts_rowid FROM files WHERE path = ?", (result.path,)
                    ).fetchone()
                    if row is not None:
                        by_rowid[row[0]] = result
                found = self._excerpts(self._conn, fts_q, list(by_rowid))
            except sqlite3.Error as e:
                logger.debug("Keyword excerpts failed: %s", e)
                return
        for rowid, (_, excerpt) in found.items():
            by_rowid[rowid].content_excerpt = excerpt

    @staticmethod
    def _paths(conn: sqlite3.Connection, rowids: list[int]) -> dict[int, tuple[str, str]]:
        """``{rowid: (path, "")}``: a direct read by rowid, no full-text match."""
        out = {}
        for rowid in rowids:
            row = conn.execute("SELECT path FROM notes_fts WHERE rowid = ?", (rowid,)).fetchone()
            if row is not None:
                out[rowid] = (row[0], "")
        return out

    @staticmethod
    def _excerpts(
        conn: sqlite3.Connection, fts_q: str, rowids: list[int]
    ) -> dict[int, tuple[str, str]]:
        """``{rowid: (path, snippet)}`` for the given rows of a match.

        Two ways to reach those rows, and which is cheaper depends on how many
        there are.  A seek per row re-opens the query each time (about 0.1 ms on
        1,000 notes, 0.35 ms on 10,000 and up).  One pass over every matching
        row, building a snippet only for the wanted ones, costs about 0.2 ms per
        1,000 notes however many are wanted.
        """
        if not rowids:
            return {}
        # The newest rowid stands in for the number of notes; it is never smaller.
        newest = conn.execute("SELECT MAX(id) FROM notes_fts_docsize").fetchone()[0] or 0
        if len(rowids) <= max(_SEEK_AT_MOST, newest // _NOTES_PER_SEEK):
            rows = [
                (rowid, *hit)
                for rowid in rowids
                for hit in conn.execute(_EXCERPT_SQL, (fts_q, rowid))
            ]
        else:
            marks = ",".join("?" * len(rowids))
            rows = conn.execute(
                # "+rowid" keeps this one pass over the match instead of a seek per row
                f"""SELECT rowid, path, snippet(notes_fts, 1, '', '', '...', 48)
                    FROM notes_fts WHERE notes_fts MATCH ? AND +rowid IN ({marks})""",  # nosec B608 - constant placeholders
                (fts_q, *rowids),
            ).fetchall()
        return {rowid: (path, snip) for rowid, path, snip in rows}
