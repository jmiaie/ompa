"""Property-based tamper tests (Hypothesis): *every* generated edit, deletion,
reorder or truncation of a valid audit log must fail verification."""

from __future__ import annotations

import shutil
import sqlite3
import tempfile
from pathlib import Path

import pytest

hypothesis = pytest.importorskip("hypothesis")
from hypothesis import HealthCheck, given, settings  # noqa: E402
from hypothesis import strategies as st  # noqa: E402

from ompa.audit import AuditLog  # noqa: E402

N = 40  # 1 genesis + 40 entries
TEXT_COLS = ["ts", "actor", "action", "body", "body_hash", "prev_hash", "hash"]
SETTINGS = settings(
    max_examples=150,
    deadline=None,
    derandomize=True,  # reproducible in CI
    suppress_health_check=[HealthCheck.function_scoped_fixture, HealthCheck.too_slow],
)


@pytest.fixture(scope="module")
def base_dir():
    d = Path(tempfile.mkdtemp(prefix="ompa-prop-base-"))
    log = AuditLog(d / "audit.sqlite3", checkpoint_interval=8)
    for i in range(N):
        log.append("fact.add", {"subject": f"s{i}", "predicate": "p", "object": f"o{i}"})
    log.close()
    assert AuditLog(d / "audit.sqlite3", readonly=True).verify().ok
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _copy(base: Path) -> Path:
    d = Path(tempfile.mkdtemp(prefix="ompa-prop-"))
    for p in base.iterdir():
        shutil.copy2(p, d / p.name)
    return d / "audit.sqlite3"


def _detected(path: Path) -> bool:
    try:
        log = AuditLog(path, readonly=True)
        try:
            return not log.verify().ok
        finally:
            log.close()
    except Exception:
        return True  # an unreadable / refused log is a detection, never a silent pass
    finally:
        shutil.rmtree(path.parent, ignore_errors=True)


@SETTINGS
@given(
    seq=st.integers(1, N + 1),
    col=st.sampled_from(TEXT_COLS),
    pos=st.integers(0, 10_000),
    ch=st.characters(codec="utf-8", exclude_characters="\x00"),
)
def test_any_single_char_edit_detected(base_dir, seq, col, pos, ch):
    path = _copy(base_dir)
    c = sqlite3.connect(str(path))
    val = c.execute(f"SELECT {col} FROM audit_entries WHERE seq=?", (seq,)).fetchone()[0]  # nosec B608
    i = pos % len(val)
    if val[i] == ch:
        ch = "0" if ch != "0" else "1"
    c.execute(f"UPDATE audit_entries SET {col}=? WHERE seq=?", (val[:i] + ch + val[i + 1 :], seq))  # nosec B608
    c.commit()
    c.close()
    assert _detected(path)


@SETTINGS
@given(rows=st.sets(st.integers(1, N + 1), min_size=1, max_size=N + 1))
def test_any_row_deletion_detected(base_dir, rows):
    path = _copy(base_dir)
    c = sqlite3.connect(str(path))
    c.executemany("DELETE FROM audit_entries WHERE seq=?", [(r,) for r in rows])
    c.commit()
    c.close()
    assert _detected(path)


@SETTINGS
@given(data=st.data())
def test_any_reorder_detected(base_dir, data):
    lo = data.draw(st.integers(1, N))
    hi = data.draw(st.integers(lo + 1, N + 1))
    window = list(range(lo, hi + 1))
    perm = data.draw(st.permutations(window))
    if perm == window:
        perm = window[1:] + window[:1]
    path = _copy(base_dir)
    c = sqlite3.connect(str(path))
    for s in window:  # move out of the way first (seq is the primary key)
        c.execute("UPDATE audit_entries SET seq=? WHERE seq=?", (-s, s))
    for old, new in zip(window, perm, strict=True):
        c.execute("UPDATE audit_entries SET seq=? WHERE seq=?", (new, -old))
    c.commit()
    c.close()
    assert _detected(path)


@SETTINGS
@given(frac=st.floats(0.0, 0.999))
def test_any_file_truncation_detected(base_dir, frac):
    path = _copy(base_dir)
    size = path.stat().st_size
    with open(path, "r+b") as f:
        f.truncate(int(size * frac))
    assert _detected(path)
