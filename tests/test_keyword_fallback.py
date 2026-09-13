"""Regression: the keyword fallback must match query TERMS, not the whole query.

Before this, `_keyword_search` tested `query_lower in text.lower()` — the entire query as
one substring. So "SAFE convention post-money" matched nothing while "SAFE" and
"post-money" each found the same note. Since semantic search needs the ompa[semantic]
extra, a default install had ONLY this fallback, and its failure was silent: an empty
table with no message.

These tests pin both directions: a multi-word query must find the note, and an
unrelated query must still find nothing. A fallback that returns everything is as
useless as one that returns nothing.
"""
import tempfile

import pytest

from ompa.semantic import SemanticIndex

NOTE = (
    "# Sprint decision\n\n"
    "The SAFE convention was aligned to POST-money. Fundraise-advisor and\n"
    "seed-instrument-modeler now both model post-money.\n"
)


@pytest.fixture
def vault(tmp_path):
    (tmp_path / "work" / "active").mkdir(parents=True)
    (tmp_path / "work" / "active" / "decision.md").write_text(NOTE)
    return tmp_path


def _idx(vault):
    return SemanticIndex(index_path=vault / ".palace" / "semantic_index")


@pytest.mark.parametrize("query", [
    "SAFE convention post-money",   # the exact query that returned nothing
    "convention SAFE",              # reordered
    "post-money modeler",           # terms spread across lines
    "safe",                         # single term, lowercase
    "POST-MONEY",                   # case insensitive
])
def test_multiword_query_finds_the_note(vault, query):
    """Every one of these must match. The first is the reported failure."""
    hits = _idx(vault).search(query)
    assert hits, f"query {query!r} found nothing -- fallback is substring-matching again"
    assert any("decision" in h.path for h in hits)


@pytest.mark.parametrize("query", ["xyzzy", "blockchain quantum", "unrelated alpha beta"])
def test_unrelated_query_finds_nothing(vault, query):
    """Discrimination: a fallback that matches everything is not a search."""
    assert _idx(vault).search(query) == []


def test_partial_term_match_scores_lower_than_full_match(vault):
    """Score reflects how many terms matched, so ranking is meaningful."""
    idx = _idx(vault)
    full = idx.search("SAFE convention post-money")
    partial = idx.search("SAFE blockchain")
    assert full and partial
    assert full[0].score > partial[0].score, (
        f"expected full match ({full[0].score}) > partial ({partial[0].score})"
    )
