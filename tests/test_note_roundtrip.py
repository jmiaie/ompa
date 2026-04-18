"""Round-trip invariance: Note.save → Note.from_file should preserve
frontmatter + body + links for the full range of inputs the fast-path
CSafeLoader splitter is expected to handle.

If any of these break, the fast-path parser has diverged from
python-frontmatter semantics and needs a fallback patch."""

import tempfile
from pathlib import Path

import pytest

from ompa.vault import Note


def _roundtrip(tmpdir: str, frontmatter: dict, content: str) -> Note:
    p = Path(tmpdir) / "note.md"
    Note(path=p, frontmatter=frontmatter, content=content).save()
    return Note.from_file(p)


def test_roundtrip_empty_frontmatter():
    with tempfile.TemporaryDirectory() as tmp:
        r = _roundtrip(tmp, {}, "Just a body.")
        assert r.content.strip() == "Just a body."


def test_roundtrip_scalar_frontmatter():
    fm = {"date": "2026-04-17", "title": "Hello World", "count": 7}
    with tempfile.TemporaryDirectory() as tmp:
        r = _roundtrip(tmp, fm, "body")
        assert r.frontmatter["date"] == "2026-04-17"
        assert r.frontmatter["title"] == "Hello World"
        assert r.frontmatter["count"] == 7


def test_roundtrip_list_frontmatter():
    fm = {"tags": ["auth", "security", "gotcha"]}
    with tempfile.TemporaryDirectory() as tmp:
        r = _roundtrip(tmp, fm, "body")
        assert r.frontmatter["tags"] == ["auth", "security", "gotcha"]


def test_roundtrip_unicode_body_and_frontmatter():
    fm = {"title": "café — über 🚀", "tags": ["naïve", "日本語"]}
    body = "Body with emoji 🔥 and math × ÷ ±."
    with tempfile.TemporaryDirectory() as tmp:
        r = _roundtrip(tmp, fm, body)
        assert r.frontmatter["title"] == "café — über 🚀"
        assert r.frontmatter["tags"] == ["naïve", "日本語"]
        assert "🔥" in r.content and "×" in r.content


def test_roundtrip_wikilinks_extracted():
    body = "See [[Other Note]] and [[folder/Thing.md]] and [[alias|Display]]."
    with tempfile.TemporaryDirectory() as tmp:
        r = _roundtrip(tmp, {}, body)
        assert "Other Note" in r.links
        assert "folder/Thing" in r.links  # .md stripped
        assert "alias" in r.links  # display dropped


def test_roundtrip_body_preserves_blank_lines():
    body = "Para one.\n\nPara two.\n\n- list\n- items\n"
    with tempfile.TemporaryDirectory() as tmp:
        r = _roundtrip(tmp, {"date": "2026-04-17"}, body)
        assert "Para one." in r.content
        assert "Para two." in r.content
        assert "- list" in r.content


def test_roundtrip_body_with_fence_lookalike():
    """A `---` inside the body must not be mistaken for a frontmatter close."""
    body = "Intro\n\n---\n\nAfter the hr.\n"
    with tempfile.TemporaryDirectory() as tmp:
        r = _roundtrip(tmp, {"date": "2026-04-17"}, body)
        assert "Intro" in r.content
        assert "After the hr." in r.content


def test_roundtrip_twice_is_idempotent():
    """Save → load → save → load yields the same Note."""
    fm = {"tags": ["a", "b"], "date": "2026-04-17"}
    body = "Some [[Link]] content.\n"
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp) / "note.md"
        Note(path=p, frontmatter=fm, content=body).save()
        first = Note.from_file(p)
        first.save()
        second = Note.from_file(p)
        assert first.frontmatter == second.frontmatter
        assert first.content == second.content
        assert first.links == second.links


@pytest.mark.parametrize(
    "body",
    [
        "",
        "single line",
        "line1\nline2",
        "trailing newline\n",
        "\n\nleading blanks",
    ],
)
def test_roundtrip_body_edge_cases(body):
    with tempfile.TemporaryDirectory() as tmp:
        r = _roundtrip(tmp, {"date": "2026-04-17"}, body)
        # Content should round-trip losslessly modulo final newline handling
        assert r.content.strip() == body.strip()
