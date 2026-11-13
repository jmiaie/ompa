"""
Date-aware ranking (opt-in) — relative-date parser + time-window boost.

Pure standard library.  Used by ``Ompa.search(..., temporal=True)``.

The parser recognises **point-in-time** references only ("two weeks ago",
"last Tuesday", "yesterday", "last weekend", "last week", "last month") and
turns them into inclusive ``(start, end)`` date windows relative to a
reference date.

Narrowing vs. the research prototype (decided post-hoc, after the prototype
showed small losses on multi-session / single-session-user questions with
broad ranges):

* Duration ranges are **not** recognised: "past/last N days|weeks|months|
  years", "past few months", "last two months", "past month", "past year",
  "over the past few weeks" etc. produce no window.
* Any window wider than ``MAX_WINDOW_DAYS`` (end - start) is dropped, which
  removes e.g. "two years ago".

The ranking stage fuses the normal ranked document list with the sub-list of
documents dated inside a window (reciprocal rank fusion, k=60, equal
weights).  Undated documents are never boosted, and there is deliberately no
general recency prior (measured as strongly harmful).
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Callable, Sequence
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

__all__ = [
    "MAX_WINDOW_DAYS",
    "RRF_K",
    "NoteDateCache",
    "parse_date_prefix",
    "parse_reference_date",
    "parse_relative_windows",
    "read_frontmatter_date",
    "rerank_by_time",
]

MAX_WINDOW_DAYS = 45
RRF_K = 60

_UNIT = {"day": 1, "week": 7, "month": 30, "year": 365}
_TOL = {"day": 1, "week": 3, "month": 10, "year": 45}
_NUM = {
    "a": 1,
    "an": 1,
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "a couple of": 2,
    "couple of": 2,
    "a few": 3,
    "few": 3,
}
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_AGO_RE = re.compile(
    r"\b(\d+|a couple of|couple of|a few|few|an|a|one|two|three|four|five|six|seven|"
    r"eight|nine|ten)\s+(day|week|month|year)s?\s+ago\b",
    re.IGNORECASE,
)
_LAST_WEEKDAY_RE = re.compile(r"\blast\s+(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)
_YESTERDAY_RE = re.compile(r"\byesterday\b", re.IGNORECASE)
_LAST_WEEKEND_RE = re.compile(r"\blast\s+weekend\b", re.IGNORECASE)
_LAST_WEEK_RE = re.compile(r"\blast\s+week\b", re.IGNORECASE)
_LAST_MONTH_RE = re.compile(r"\blast\s+month\b", re.IGNORECASE)
_DATE_PREFIX_RE = re.compile(r"(\d{4})[-/](\d{2})[-/](\d{2})")


def _days(n: int) -> timedelta:
    return timedelta(days=n)


def parse_relative_windows(query: str, reference: date) -> list[tuple[date, date]]:
    """Return inclusive ``(start, end)`` windows for point-in-time references.

    Rules (case-insensitive, word boundaries; ``n`` from digits or words):

    1. ``N (day|week|month|year)s? ago`` -> centre ``ref - n*UNIT``,
       half-width ``min(TOL * (n if unit != day else 1), 2*TOL)``.
    2. ``last <weekday>`` -> the most recent such weekday strictly before
       ``ref``, +/- 1 day.
    3. ``yesterday`` -> ``(ref - 2, ref)``.
    4. ``last weekend`` -> the most recent Saturday strictly before ``ref``,
       ``(sat - 1, sat + 2)``.
    5. ``last week`` -> ``(ref - 11, ref - 3)``.
    6. ``last month`` -> ``(ref - 45, ref - 15)``.

    Windows wider than ``MAX_WINDOW_DAYS`` are dropped.  Returns ``[]`` when
    nothing matches; never raises.
    """
    try:
        if not isinstance(query, str) or not query:
            return []
        if isinstance(reference, datetime):
            reference = reference.date()
        if not isinstance(reference, date):
            return []
        ref = reference
        windows: list[tuple[date, date]] = []

        def add(start_off: int, end_off: int) -> None:
            # Offsets in days relative to ref; out-of-range dates are skipped.
            with contextlib.suppress(OverflowError, ValueError):
                windows.append((ref + _days(start_off), ref + _days(end_off)))

        for m in _AGO_RE.finditer(query):
            word = m.group(1).lower()
            n = int(word) if word.isdigit() else _NUM[word]
            unit = m.group(2).lower()
            centre = -n * _UNIT[unit]
            half = min(_TOL[unit] * (n if unit != "day" else 1), 2 * _TOL[unit])
            add(centre - half, centre + half)

        for m in _LAST_WEEKDAY_RE.finditer(query):
            wd = _WEEKDAYS.index(m.group(1).lower())
            delta = (ref.weekday() - wd) % 7 or 7
            add(-delta - 1, -delta + 1)

        if _YESTERDAY_RE.search(query):
            add(-2, 0)

        if _LAST_WEEKEND_RE.search(query):
            delta = (ref.weekday() - 5) % 7 or 7
            add(-delta - 1, -delta + 2)

        if _LAST_WEEK_RE.search(query):
            add(-11, -3)

        if _LAST_MONTH_RE.search(query):
            add(-45, -15)

        return [(a, b) for a, b in windows if (b - a).days <= MAX_WINDOW_DAYS]
    except Exception:  # the parser must never break search
        return []


def parse_date_prefix(value: Any) -> date | None:
    """Parse a leading ``YYYY-MM-DD`` or ``YYYY/MM/DD``; ``None`` if absent/invalid."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        return None
    m = _DATE_PREFIX_RE.match(value.strip().strip("'\""))
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def parse_reference_date(value: str | date | datetime | None) -> date:
    """Normalise a ``reference_date`` argument (default: today).

    Accepts ``"YYYY-MM-DD"``, ``"YYYY/MM/DD"``, :class:`date` or
    :class:`datetime`.  Raises ``ValueError`` for anything else.
    """
    if value is None:
        return date.today()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and re.fullmatch(r"\s*\d{4}[-/]\d{2}[-/]\d{2}\s*", value):
        parsed = parse_date_prefix(value)
        if parsed is not None:
            return parsed
    raise ValueError(f"Invalid reference_date {value!r}: expected YYYY-MM-DD, YYYY/MM/DD or a date")


# Frontmatter is read line by line and only up to these limits.
_MAX_FM_LINES = 200
_MAX_FM_BYTES = 64 * 1024
_FM_DATE_RE = re.compile(r"^date\s*:\s*(.*?)\s*$")


def read_frontmatter_date(path: str | Path) -> date | None:
    """Return the note's YAML frontmatter ``date`` (leading date only), or ``None``.

    Reads only the frontmatter block (bounded), never the whole note.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            first = f.readline(_MAX_FM_BYTES)
            if first.lstrip("﻿").rstrip() != "---":
                return None
            read = len(first)
            for _ in range(_MAX_FM_LINES):
                line = f.readline(_MAX_FM_BYTES)
                if not line:
                    return None
                read += len(line)
                if read > _MAX_FM_BYTES:
                    return None
                stripped = line.rstrip("\r\n")
                if stripped.rstrip() in ("---", "..."):
                    return None
                m = _FM_DATE_RE.match(stripped)
                if m:
                    return parse_date_prefix(m.group(1))
    except (OSError, ValueError):
        return None
    return None


class NoteDateCache:
    """Frontmatter-date lookup cached by ``(path, mtime_ns)``.

    Only paths that resolve inside one of ``roots`` are read; anything else
    is treated as undated.
    """

    def __init__(self, roots: Sequence[str | Path] = (), max_entries: int = 50_000):
        self._roots = [Path(r).resolve() for r in roots]
        self._max = max_entries
        self._cache: dict[str, tuple[int, date | None]] = {}

    def _inside(self, p: Path) -> bool:
        return any(p == r or p.is_relative_to(r) for r in self._roots)

    def get(self, path: str | Path) -> date | None:
        try:
            p = Path(path).resolve()
            if not self._inside(p) or not p.is_file():
                return None
            mtime = p.stat().st_mtime_ns
        except (OSError, ValueError, RuntimeError):
            return None
        key = str(p)
        hit = self._cache.get(key)
        if hit is not None and hit[0] == mtime:
            return hit[1]
        value = read_frontmatter_date(p)
        if len(self._cache) >= self._max:
            self._cache.clear()
        self._cache[key] = (mtime, value)
        return value

    __call__ = get


def rerank_by_time(
    head: Sequence[Any],
    pool: Sequence[Any],
    windows: Sequence[tuple[date, date]],
    date_of: Callable[[str], date | None],
    limit: int,
    k: int = RRF_K,
) -> list[Any]:
    """Time-window boost over search results (objects with ``path``/``score``).

    ``head`` is the normal top-``limit`` result list, ``pool`` the same search
    run deeper.  Base document order = distinct paths of ``head``, then
    distinct paths of ``pool`` not in head.  If no base document is dated
    inside a window, ``head`` is returned unchanged (same list object).
    Otherwise documents are ordered by RRF over ``[base, in_window]`` (ties by
    base position); each document contributes its head entries if present,
    else its pool entries; the output is truncated to ``limit`` and each
    result's ``score`` is set to its document's fused score.
    """
    if not windows:
        return list(head) if not isinstance(head, list) else head

    groups: dict[str, list[Any]] = {}
    base: list[str] = []
    for r in head:
        if r.path not in groups:
            groups[r.path] = []
            base.append(r.path)
        groups[r.path].append(r)
    in_head = set(base)
    for r in pool:
        if r.path in in_head:
            continue
        if r.path not in groups:
            groups[r.path] = []
            base.append(r.path)
        groups[r.path].append(r)

    inw = []
    for p in base:
        d = date_of(p)
        if d is not None and any(a <= d <= b for a, b in windows):
            inw.append(p)
    if not inw:
        return head if isinstance(head, list) else list(head)

    fused = {p: 1.0 / (k + rank + 1) for rank, p in enumerate(base)}
    for rank, p in enumerate(inw):
        fused[p] += 1.0 / (k + rank + 1)
    position = {p: i for i, p in enumerate(base)}
    order = sorted(base, key=lambda p: (-fused[p], position[p]))

    out: list[Any] = []
    for p in order:
        for r in groups[p]:
            if len(out) >= limit:
                return out
            r.score = fused[p]
            out.append(r)
    return out
