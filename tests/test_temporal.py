"""Opt-in date-aware ranking (ompa.temporal + Ompa.search(temporal=True)); stdlib only."""

from datetime import date, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from ompa import Ompa
from ompa.temporal import (
    MAX_WINDOW_DAYS,
    NoteDateCache,
    parse_date_prefix,
    parse_reference_date,
    parse_relative_windows,
    read_frontmatter_date,
    rerank_by_time,
)

REF = date(2023, 5, 30)  # a Tuesday


def W(q, ref=REF):
    return parse_relative_windows(q, ref)


def test_n_units_ago():
    assert W("what did I buy 3 days ago?") == [(date(2023, 5, 26), date(2023, 5, 28))]
    assert W("Two weeks ago I met someone") == [(date(2023, 5, 10), date(2023, 5, 22))]
    assert W("a week ago") == [(date(2023, 5, 20), date(2023, 5, 26))]
    assert W("a couple of days ago") == [(date(2023, 5, 27), date(2023, 5, 29))]
    assert W("a few weeks ago") == [(date(2023, 5, 3), date(2023, 5, 15))]
    assert W("two months ago") == [(date(2023, 3, 11), date(2023, 4, 20))]


def test_last_weekday_weekend_yesterday():
    assert W("last tuesday") == [(date(2023, 5, 22), date(2023, 5, 24))]
    assert W("last Friday") == [(date(2023, 5, 25), date(2023, 5, 27))]
    assert W("yesterday") == [(date(2023, 5, 28), date(2023, 5, 30))]
    assert W("last weekend") == [(date(2023, 5, 26), date(2023, 5, 29))]
    assert W("last weekend", date(2023, 6, 3)) == [(date(2023, 5, 26), date(2023, 5, 29))]


def test_last_week_and_month():
    assert W("last week") == [(date(2023, 5, 19), date(2023, 5, 27))]
    assert W("last month") == [(date(2023, 4, 15), date(2023, 5, 15))]


@pytest.mark.parametrize(
    "q",
    [
        "in the past few months",
        "over the last two months",
        "the past month",
        "past 2 weeks",
        "last 3 months",
        "two years ago",
        "over the past few weeks",
        "how many hotels have I stayed at?",
        "",
        "ago ago last",
        "9999999999 years ago",
        "last weekday",
    ],
)
def test_negatives(q):
    assert W(q) == []


def test_never_raises_and_cap():
    assert parse_relative_windows(None, REF) == []  # type: ignore[arg-type]
    assert parse_relative_windows("yesterday", "not a date") == []  # type: ignore[arg-type]
    assert all((b - a).days <= MAX_WINDOW_DAYS for a, b in W("3 months ago, last month, 2 weeks ago"))


def test_date_helpers(tmp_path):
    assert parse_date_prefix("2023/05/20 (Sat) 02:21") == date(2023, 5, 20)
    assert parse_date_prefix("2023-02-30") is None and parse_date_prefix(None) is None
    assert parse_reference_date("2023/05/20") == date(2023, 5, 20)
    assert parse_reference_date(datetime(2023, 5, 20, 3)) == date(2023, 5, 20)
    assert parse_reference_date(None) == date.today()
    with pytest.raises(ValueError):
        parse_reference_date("yesterday")
    p = tmp_path / "n.md"
    p.write_text("---\ntags: [x]\ndate: '2023-05-20'\n---\nbody\n")
    assert read_frontmatter_date(p) == date(2023, 5, 20)
    p.write_text("no frontmatter\ndate: 2023-05-20\n")
    assert read_frontmatter_date(p) is None
    cache = NoteDateCache([tmp_path / "vault"])
    assert cache.get(p) is None  # outside the vault roots: never read


def _r(path, score=1.0):
    return SimpleNamespace(path=path, score=score)


def test_rerank_by_time_math():
    head = [_r("a"), _r("b"), _r("a")]
    pool = [_r("a"), _r("b"), _r("c"), _r("d")]
    dates = {"a": None, "b": date(2023, 1, 1), "c": date(2023, 5, 20), "d": date(2023, 5, 21)}
    win = [(date(2023, 5, 19), date(2023, 5, 22))]
    out = rerank_by_time(head, pool, win, dates.get, limit=10)
    # base a,b,c,d ; in-window c(0), d(1): c = 1/63+1/61, d = 1/64+1/62 > a = 1/61
    assert [r.path for r in out] == ["c", "d", "a", "a", "b"]
    assert out[0].score == pytest.approx(1 / 63 + 1 / 61)
    assert rerank_by_time(head, pool, [(date(2020, 1, 1), date(2020, 1, 2))], dates.get, 3) is head
    assert rerank_by_time(head, pool, [], dates.get, 3) is head


NOTES = {
    "old-trip": ("2023-01-02", "We went hiking in the mountains and the trip was great."),
    "mid-trip": ("2023-05-16", "Short note: hiking trip planned, booked a cabin."),
    "chat": (None, "Hiking trip ideas and the mountains, hiking hiking trip."),
    "other": ("2023-05-15", "Groceries and cooking dinner."),
}


@pytest.fixture()
def ao(tmp_path):
    o = Ompa(vault_path=str(tmp_path / "vault"), enable_semantic=False)
    for name, (dt, body) in NOTES.items():
        o.write(content=body, file_path=f"work/{name}.md", tags=["t"], date=dt)
    # an undated note (no frontmatter date at all)
    (Path(o.vault.vault_path) / "work" / "chat.md").write_text(NOTES["chat"][1] + "\n")
    return o


def _names(rs):
    return list(dict.fromkeys(Path(r.path).stem for r in rs))


def test_search_default_off_identical(ao):
    q = "what hiking trip did I take two weeks ago?"
    base = ao.search(q, limit=10, citations=False)
    off = ao.search(q, limit=10, citations=False, temporal=False)
    assert [(r.path, r.score) for r in off] == [(r.path, r.score) for r in base]
    assert _names(base)[0] != "mid-trip"


def test_search_boosts_in_window(ao):
    q = "what hiking trip did I take two weeks ago?"
    rs = ao.search(q, limit=10, temporal=True, reference_date="2023-05-30")
    assert _names(rs)[0] == "mid-trip"
    assert rs[0].citation is not None  # citations still attached


def test_search_no_phrase_identical(ao):
    q = "hiking trip mountains"
    base = [(r.path, r.score) for r in ao.search(q, limit=10, citations=False)]
    on = ao.search(q, limit=10, citations=False, temporal=True, reference_date=REF)
    assert [(r.path, r.score) for r in on] == base


def test_undated_never_boosted(ao):
    q = "hiking trip ideas yesterday"
    base = _names(ao.search(q, limit=10, citations=False))
    got = _names(ao.search(q, limit=10, citations=False, temporal=True, reference_date="2023-01-03"))
    assert got[0] == "old-trip"
    assert [n for n in got if n != "old-trip"] == [n for n in base if n != "old-trip"]


def test_constructor_default(tmp_path, ao):
    on = Ompa(vault_path=str(ao.vault.vault_path), enable_semantic=False, temporal_ranking=True)
    q = "what hiking trip did I take two weeks ago?"
    assert _names(on.search(q, limit=10, reference_date="2023-05-30"))[0] == "mid-trip"


def test_write_date(tmp_path):
    o = Ompa(vault_path=str(tmp_path / "v"), enable_semantic=False)
    res = o.write(content="hello", file_path="work/x.md", date="2023-05-20")
    assert read_frontmatter_date(res["path"]) == date(2023, 5, 20)
    for bad in ("2023/05/20", "2023-02-30", "yesterday", 20230520):
        with pytest.raises(ValueError):
            o.write(content="hello", file_path="work/y.md", date=bad)
    res = o.write(content="hello", file_path="work/z.md")
    assert read_frontmatter_date(res["path"]) == date.today()
