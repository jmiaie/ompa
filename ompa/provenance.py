"""
Provenance / citations for retrieval results (audit-memory).

A citation pins a search hit to *exact bytes*: the note path, the line and
byte span of the matched chunk, the SHA-256 of the note content the span was
read from, and author / time metadata.  Citations are computed when a result
is returned, from the note file itself, so they always describe the content a
reader can open and re-hash.  ``stale=True`` means the note changed after it
was indexed (the score may describe older text; the span describes the
current file).

Times: ``recorded_at`` is transaction time (when the content was written, from
the audit log when enabled, else the file mtime); ``valid_from`` /
``valid_to`` are valid time from the note's frontmatter (``date`` /
``valid_from`` and ``valid_to`` / ``expires``).
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .semantic import CHUNK_WORDS

_FM_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_WORD_RE = re.compile(r"\S+")


def _frontmatter_fields(text: str) -> tuple[dict, int]:
    """Cheap scalar frontmatter read (no YAML dependency).  Returns (fields, body_offset)."""
    m = _FM_RE.match(text)
    if not m:
        return {}, 0
    out: dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" not in line or line.startswith((" ", "\t", "-")):
            continue
        k, _, v = line.partition(":")
        v = v.strip().strip("'\"")
        if v:
            out[k.strip().lower()] = v
    return out, m.end()


def _span(text: str, start: int, end: int) -> dict:
    return {
        "line_start": text.count("\n", 0, start) + 1,
        "line_end": text.count("\n", 0, max(start, end - 1)) + 1,
        "byte_start": len(text[:start].encode("utf-8")),
        "byte_end": len(text[:end].encode("utf-8")),
    }


def _chunk_span(text: str, chunk_index: int) -> tuple[int, int] | None:
    """Char span of the chunk starting at word ``chunk_index`` (CHUNK_WORDS-word chunks)."""
    start = end = None
    last = chunk_index + CHUNK_WORDS - 1
    for n, m in enumerate(_WORD_RE.finditer(text)):
        if n == chunk_index:
            start = m.start()
        if n >= chunk_index:
            end = m.end()
        if n >= last:
            break
    return (start, end) if start is not None else None


def _query_span(text: str, body_off: int, excerpt: str | None, query: str | None):
    """Char span of the line with the most query words (else the excerpt's line)."""
    words = {w.lower() for w in re.findall(r"\w+", query or "") if len(w) > 2}
    if words:
        best, best_n, pos = None, 0, 0
        for line in text[body_off:].splitlines(keepends=True):
            low = line.lower()
            n = sum(1 for w in words if w in low)
            if n > best_n:
                s = body_off + pos
                best, best_n = (s, s + len(line.rstrip("\r\n"))), n
                if n == len(words):
                    break
            pos += len(line)
        if best is not None:
            return best
    probe = (excerpt or "").replace("...", " ").strip()
    frag = " ".join(probe.split()[:6])
    i = text.find(frag, body_off) if frag else -1
    if i >= 0:
        ls = text.rfind("\n", 0, i) + 1
        le = text.find("\n", i)
        return ls, (le if le >= 0 else len(text))
    return body_off, len(text)


def cite(
    path: str | Path,
    *,
    chunk_index: int | None = None,
    excerpt: str | None = None,
    query: str | None = None,
    indexed_sig: list[int] | None = None,
    recorded: dict | None = None,
) -> dict | None:
    """Build a citation for a hit in ``path`` (None if the file is unreadable).

    ``recorded`` is the latest audit record for the note (``{"ts", "actor", "digest"}``).
    """
    p = Path(path)
    try:
        raw = p.read_bytes()
        st = p.stat()
    except OSError:
        return None
    text = raw.decode("utf-8", errors="replace")
    fields, body_off = _frontmatter_fields(text)
    span = _chunk_span(text, chunk_index) if chunk_index is not None else None
    if span is None:
        span = _query_span(text, body_off, excerpt, query)
    sha = hashlib.sha256(raw).hexdigest()
    cit = {
        "path": str(p),
        **_span(text, *span),
        "content_sha256": sha,
        "author": fields.get("author") or fields.get("agent"),
        "recorded_at": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(
            timespec="seconds"
        ),
        "recorded_source": "mtime",
        "valid_from": fields.get("valid_from") or fields.get("date"),
        "valid_to": fields.get("valid_to") or fields.get("expires"),
        "stale": bool(indexed_sig) and list(indexed_sig) != [st.st_mtime_ns, st.st_size],
    }
    if recorded:
        cit["recorded_at"] = recorded.get("ts")
        cit["recorded_source"] = "audit"
        cit["recorded_by"] = recorded.get("actor")
        cit["author"] = cit["author"] or recorded.get("actor")
        cit["audited_sha256"] = recorded.get("digest")
        cit["matches_audit"] = recorded.get("digest") == sha
    return cit
