"""The fast paths return what the slow ones did.

1.2.1 makes vault walks, vault statistics, keyword search and the MCP server's
staleness check cheaper.  Each test here pins the new code to the behaviour of
the code it replaced, written out below as the reference.
"""

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ompa import Ompa, mcp_server
from ompa.keyword import KeywordIndex, _fts_query
from ompa.semantic import SemanticIndex, fuse_rrf, scan_vault
from ompa.vault import Note, Vault, is_excluded, walk_notes

# ---------------------------------------------------------------------------
# walk_notes == rglob("*.md") filtered through is_excluded
# ---------------------------------------------------------------------------


def _reference_walk(root, patterns=None) -> list[str]:
    root = Path(root)
    return [str(p) for p in root.rglob("*.md") if not is_excluded(p, root, patterns)]


@pytest.fixture
def messy_tree(tmp_path):
    root = tmp_path / "vault"
    files = [
        "top.md",
        "README.md",
        "notes.txt",
        "UPPER.MD",
        "brain/North Star.md",
        "brain/Rethinking auth.md",
        "brain/thinking.md",
        "brain/deep/er/note.md",
        "work/active/a.md",
        "work/archive/old.md",
        "x/work/archive/older.md",
        "x/work/archived/kept.md",
        "thinking/draft.md",
        "a/thinking/b/draft.md",
        ".git/HEAD.md",
        "sub/.git/hooks/x.md",
        ".claude/memory.md",
        ".obsidian/workspace.md",
        ".palace/notes.md",
        "design-thinking/idea.md",
        "folder.md/inside.md",
        "empty-dir/.keep",
    ]
    for rel in files:
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"# {rel}\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "elsewhere.md").write_text("x", encoding="utf-8")
    try:
        (root / "linked-dir").symlink_to(outside, target_is_directory=True)
        (root / "linked-note.md").symlink_to(outside / "elsewhere.md")
        (root / "broken.md").symlink_to(outside / "missing.md")
    except OSError:  # no symlink privilege (Windows)
        pass
    return root


@pytest.mark.parametrize(
    "patterns",
    [
        None,
        [],
        [".git", ".claude", "thinking"],
        ["work/archive"],
        ["brain/deep", "x"],
        ["README.md", "thinking.md"],
        ["work\\archive", "/brain/"],
        ["a/thinking/b"],
        ["nothing-matches"],
    ],
)
def test_walk_notes_matches_rglob_and_is_excluded(messy_tree, patterns):
    walked = [path for path, _ in walk_notes(messy_tree, patterns)]
    assert sorted(walked) == sorted(_reference_walk(messy_tree, patterns))
    assert len(walked) == len(set(walked))


def test_walk_notes_spells_paths_like_pathlib(messy_tree, monkeypatch):
    monkeypatch.chdir(messy_tree)
    assert sorted(p for p, _ in walk_notes(".")) == sorted(_reference_walk("."))
    assert sorted(p for p, _ in walk_notes("brain")) == sorted(_reference_walk("brain"))
    with_slash = str(messy_tree) + os.sep
    assert sorted(p for p, _ in walk_notes(with_slash)) == sorted(_reference_walk(with_slash))


def test_walk_notes_lists_a_folder_before_its_subfolders(messy_tree):
    walked = [Path(p).relative_to(messy_tree).as_posix() for p, _ in walk_notes(messy_tree)]
    assert walked.index("brain/thinking.md") < walked.index("brain/deep/er/note.md")
    assert walked.index("top.md") < walked.index("work/active/a.md")


def test_walk_notes_never_enters_an_excluded_folder(messy_tree, monkeypatch):
    entered = []
    real_scandir = os.scandir

    def recording_scandir(path="."):
        entered.append(Path(path).relative_to(messy_tree).as_posix())
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", recording_scandir)
    list(walk_notes(messy_tree))
    assert "brain/deep/er" in entered
    for skipped in (".git", ".claude", "thinking", "a/thinking", "sub/.git", "linked-dir"):
        assert skipped not in entered


def test_walk_notes_of_a_missing_folder_is_empty(tmp_path):
    assert list(walk_notes(tmp_path / "nope")) == []


def test_scan_vault_matches_the_reference(messy_tree):
    def reference(patterns):
        out = {}
        for path in _reference_walk(messy_tree, patterns):
            try:
                st = os.stat(path)
            except OSError:
                continue
            out[path] = [st.st_mtime_ns, st.st_size]
        return out

    for patterns in (None, ["work/archive"], [".git"]):
        assert scan_vault(messy_tree, patterns) == reference(patterns)
    assert not any(p.endswith("broken.md") for p in scan_vault(messy_tree))


def test_list_notes_and_name_search_are_unchanged(messy_tree):
    vault = Vault(messy_tree)
    reference = sorted(_reference_walk(messy_tree))
    assert sorted(str(n.path) for n in vault.list_notes()) == reference
    assert sorted(str(p) for p in vault.note_paths()) == reference
    assert all(isinstance(p, Path) for p in vault.note_paths())
    hits = vault.search_by_name("THINK")
    assert sorted(n.path.name for n in hits) == ["Rethinking auth.md", "thinking.md"]
    assert all(n.content for n in hits)


# ---------------------------------------------------------------------------
# Vault statistics and orphans == the Note.from_file based computation
# ---------------------------------------------------------------------------


def _reference_stats(vault: Vault) -> dict:
    """``Vault.get_stats`` as it was in 1.1.1: every note fully parsed."""
    notes = vault.list_notes()
    index = vault._build_filename_index(notes)
    linked = set()
    for note in notes:
        for link in note.links:
            resolved = vault._resolve_wikilink(link, index)
            if resolved:
                linked.add(resolved)
    orphans = [
        n.path
        for n in notes
        if n.path not in linked and n.path.name not in ["Home.md", "README.md"]
    ]
    folder_counts: dict[str, int] = {}
    brain = 0
    for note in notes:
        folder = note.path.parent.name or "root"
        folder_counts[folder] = folder_counts.get(folder, 0) + 1
        if "brain" in note.path.parts or str(note.frontmatter.get("wing", "")).lower() == "brain":
            brain += 1
    if vault.config.brain_folder.exists():
        brain = max(brain, len(list(vault.config.brain_folder.glob("*.md"))))
    return {
        "total_notes": len(notes),
        "orphans": len(orphans),
        "folder_counts": folder_counts,
        "brain_notes": brain,
        "_orphan_paths": orphans,
    }


@pytest.fixture
def linked_vault(tmp_path):
    root = tmp_path / "vault"
    notes = {
        "Home.md": "Start at [[alpha]].",
        "README.md": "About.",
        "work/alpha.md": "---\ntags: [a]\n---\nLinks to [[beta]] and [[Gamma Note|the gamma]].",
        "work/beta.md": "No frontmatter, links to [[alpha.md]] and [[missing]].",
        "work/Gamma Note.md": "---\nwing: Brain\n---\nPoints nowhere.",
        "work/wingspan.md": "---\ntitle: wingspan of a heron\n---\nMentions wing only in a title.",
        "work/wing-list.md": "---\nwing: [brain, work]\n---\nA list where a string is expected.",
        "org/nested-list.md": "---\nrelated: [[alpha]]\n---\nValid YAML: a nested list, not a link.",
        "org/quoted-link.md": '---\nrelated: "[[delta]]"\n---\nA quoted link in frontmatter.',
        "org/broken-yaml.md": "---\nseen: [[delta]] and [[epsilon]]\n---\nBody links [[beta]].",
        "org/broken-no-links.md": "---\nkey: [unclosed\n---\nBody links [[epsilon]].",
        "org/unclosed.md": "---\ntitle: never closed\n\nBody with [[delta]].",
        "org/four-dashes.md": "----\ntitle: four\n----\nLinks to [[zeta]].",
        "org/leading-space.md": "\n\n---\ntitle: late\n---\nLinks to [[eta]].",
        "org/json.md": '{\n"wing": "brain"\n}\nJSON frontmatter, links to [[theta]].',
        "org/delta.md": "Target of frontmatter links only.",
        "org/epsilon.md": "Target.",
        "org/zeta.md": "Target.",
        "org/eta.md": "Target.",
        "org/theta.md": "Target.",
        "org/empty.md": "",
        "org/only-frontmatter.md": "---\nwing: brain\n---\n",
        "brain/Key Decisions.md": "Decided [[alpha]] over [[beta|b]].",
        "org/by-path.md": "A link by path: [[work/alpha]].",
    }
    for rel, text in notes.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    (root / "org" / "latin1.md").write_bytes(b"caf\xe9 [[alpha]]")
    (root / "org" / "crlf.md").write_bytes(b"---\r\nwing: brain\r\n---\r\nWindows [[iota]].\r\n")
    (root / "org" / "crlf-plain.md").write_bytes(b"---\r\ntitle: x\r\n---\r\nLinks [[kappa]].\r\n")
    (root / "org" / "bom.md").write_bytes(b"\xef\xbb\xbf---\ntitle: bom\n---\nLinks [[iota]].")
    for name in ("iota", "kappa"):
        (root / "org" / f"{name}.md").write_text("Target.", encoding="utf-8")
    (root / "org" / "a-folder.md").mkdir()
    return Vault(root)


def test_get_stats_matches_the_full_parse(linked_vault):
    reference = _reference_stats(linked_vault)
    orphan_paths = reference.pop("_orphan_paths")
    assert linked_vault.get_stats() == reference
    assert linked_vault.orphan_paths() == orphan_paths
    found = linked_vault.find_orphans()
    assert [n.path for n in found] == orphan_paths
    assert all(isinstance(n, Note) for n in found)
    # the cases the shortcut has to get right, spelled out
    names = {p.name for p in orphan_paths}
    assert "delta.md" not in names  # linked from malformed frontmatter, as before
    assert "alpha.md" not in names
    assert "quoted-link.md" in names


def test_links_and_wing_matches_note_from_file(linked_vault):
    for path in linked_vault.note_paths():
        note = Note.from_file(path)
        links, wing = Vault._links_and_wing(path)
        assert links == note.links, path.name
        assert wing == str(note.frontmatter.get("wing", "")), path.name


def test_stats_parse_yaml_only_where_it_matters(linked_vault, monkeypatch):
    parsed = []
    real = Note.from_file.__func__

    def counting(cls, path):
        parsed.append(Path(path).name)
        return real(cls, path)

    monkeypatch.setattr(Note, "from_file", classmethod(counting))
    linked_vault.get_stats()
    # frontmatter that mentions "wing" or holds "[[", plus JSON frontmatter
    assert sorted(parsed) == sorted(
        [
            "Gamma Note.md",
            "wingspan.md",
            "wing-list.md",
            "nested-list.md",
            "quoted-link.md",
            "broken-yaml.md",
            "json.md",
            "only-frontmatter.md",
            "crlf.md",
        ]
    )


def _expected_listing(vault: Vault) -> list[str]:
    notes = sorted(vault.list_notes(), key=lambda n: n.path)
    lines = [f"- {n.path.relative_to(vault.vault_path)}" for n in notes[:30]]
    if len(notes) > 30:
        lines.append(f"... and {len(notes) - 30} more")
    return lines


def test_session_start_lists_the_same_files(tmp_path):
    ao = Ompa(vault_path=tmp_path / "vault", enable_semantic=False)
    for i in range(45):
        ao.write(f"Note number {i} about topic-{i % 7}.", file_path=f"work/n-{i:02d} x.md")
    ao.write("Sorts by path parts, not by string.", file_path="work-b/first.md")
    output = ao.session_start().output
    listing = output[output.index("### Vault Files") :].splitlines()[1:]
    assert listing == _expected_listing(ao.vault)
    stats = ao.vault.get_stats()
    assert f"- Total notes: {stats['total_notes']}" in output
    assert f"- Orphans (no links): {stats['orphans']}" in output


def test_session_start_and_wrap_up_do_not_parse_every_note(tmp_path, monkeypatch):
    ao = Ompa(vault_path=tmp_path / "vault", enable_semantic=False)
    for i in range(40):
        ao.write(f"Plain note {i} linking to [[n-{(i + 1) % 40}]].", file_path=f"work/n-{i}.md")
    parsed = []
    real = Note.from_file.__func__
    monkeypatch.setattr(
        Note, "from_file", classmethod(lambda cls, p: parsed.append(p) or real(cls, p))
    )
    start = ao.session_start()
    stop = ao.stop()
    assert start.success and stop.success
    assert "**Orphan notes:**" in stop.output
    assert len(parsed) < 10, f"{len(parsed)} notes were fully parsed"


# ---------------------------------------------------------------------------
# Keyword search: same ranking, scores and excerpts as the one-query form
# ---------------------------------------------------------------------------

# KeywordIndex.search as it was in 1.1.1.
_ONE_QUERY = """SELECT path, bm25(notes_fts) AS s, snippet(notes_fts, 1, '', '', '...', 48)
                FROM notes_fts WHERE notes_fts MATCH ? ORDER BY s LIMIT ?"""


def _fts5() -> bool:
    try:
        sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        return True
    except sqlite3.Error:
        return False


needs_fts5 = pytest.mark.skipif(not _fts5(), reason="SQLite was built without FTS5")


@pytest.fixture
def keyword_vault(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    topics = ["postgres migration", "auth tokens", "kubernetes upgrade", "billing export"]
    for i in range(60):
        words = [f"filler{j}" for j in range(i % 9 + 3)] * (i % 4 + 1)
        topic = topics[i % len(topics)]
        body = f"Note {i} about the {topic}. " + " ".join(words) + f" the plan for item{i}. "
        (root / f"note-{i:02d}.md").write_text(body * (1 + i % 5), encoding="utf-8")
    index = KeywordIndex(root / ".palace" / "keyword_index.sqlite3")
    index.refresh(root)
    yield root, index
    index.close()


QUERIES = [
    "what was the plan for the postgres migration",
    "auth tokens",
    "the the the plan",
    "item17 kubernetes",
    "nothing-here-matches zzz",
    'quotes " and AND OR NEAR( * syntax',
]


@needs_fts5
@pytest.mark.parametrize("limit", [1, 5, 20, 100])
def test_keyword_search_matches_the_single_query(keyword_vault, limit, note_length):
    _, index = keyword_vault
    for query in QUERIES:
        got = index.search(query, limit)
        want = index._conn.execute(_ONE_QUERY, (_fts_query(query), limit)).fetchall()
        assert len(got) == len(want)
        assert [round(r.score, 9) for r in got] == [round(-s, 9) for _, s, _ in want]
        # ties may come out in either order; compare the notes score by score
        assert sorted((round(r.score, 9), r.path, r.content_excerpt) for r in got) == sorted(
            (round(-s, 9), p, snip) for p, s, snip in want
        )
        assert all(r.match_type == "keyword" for r in got)


@pytest.fixture(params=["short notes", "long notes"])
def note_length(request, monkeypatch):
    """Run a keyword test down both of ``KeywordIndex.search``'s paths."""
    import ompa.keyword as keyword_module

    long_notes = request.param == "long notes"
    monkeypatch.setattr(keyword_module, "_LONG_NOTE_BYTES", 0 if long_notes else 10**12)
    return request.param


@needs_fts5
def test_long_notes_are_recognised(tmp_path):
    def index_of(words_per_note):
        root = tmp_path / f"v{words_per_note}"
        root.mkdir()
        for i in range(30):
            body = " ".join(f"word{(i * 31 + j * 7) % 900}" for j in range(words_per_note))
            (root / f"n{i}.md").write_text(body, encoding="utf-8")
        index = KeywordIndex(root / ".palace" / "keyword_index.sqlite3")
        index.refresh(root)
        return index

    short, long = index_of(60), index_of(2000)
    assert not KeywordIndex._long_notes(short._conn)
    assert KeywordIndex._long_notes(long._conn)
    empty = KeywordIndex(tmp_path / "empty" / "keyword_index.sqlite3")
    assert not KeywordIndex._long_notes(empty._conn)
    for index in (short, long, empty):
        index.close()


@needs_fts5
@pytest.mark.parametrize("seek_at_most", [0, 10**9])
def test_both_ways_of_fetching_excerpts_agree(keyword_vault, monkeypatch, seek_at_most):
    """A seek per result, or one pass over the match: the same excerpts either way."""
    import ompa.keyword as keyword_module

    _, index = keyword_vault
    monkeypatch.setattr(keyword_module, "_LONG_NOTE_BYTES", 0)
    monkeypatch.setattr(keyword_module, "_SEEK_AT_MOST", seek_at_most)
    monkeypatch.setattr(keyword_module, "_NOTES_PER_SEEK", 10**9)
    for query in QUERIES:
        for limit in (3, 40):
            got = index.search(query, limit)
            want = index._conn.execute(_ONE_QUERY, (_fts_query(query), limit)).fetchall()
            assert sorted((round(r.score, 9), r.path, r.content_excerpt) for r in got) == sorted(
                (round(-s, 9), p, snip) for p, s, snip in want
            )


@needs_fts5
def test_keyword_search_without_excerpts_then_fill(keyword_vault, note_length):
    _, index = keyword_vault
    query = QUERIES[0]
    full = index.search(query, 10)
    bare = index.search(query, 10, excerpts=False)
    assert [(r.path, r.score) for r in bare] == [(r.path, r.score) for r in full]
    assert all(r.content_excerpt == "" for r in bare)
    kept = bare[2:5]
    index.fill_excerpts(query, kept)
    assert [r.content_excerpt for r in kept] == [r.content_excerpt for r in full[2:5]]
    assert bare[0].content_excerpt == ""  # only what was asked for


@needs_fts5
def test_keyword_search_is_stable_across_calls(keyword_vault, note_length):
    _, index = keyword_vault
    first = [(r.path, r.score) for r in index.search("the plan", 20)]
    assert all([(r.path, r.score) for r in index.search("the plan", 20)] == first for _ in range(5))


class _BagOfWords:
    def encode(self, text):
        vec = [0.0] * 64
        for word in text.lower().split():
            vec[sum(map(ord, word)) % 64] += 1.0
        return vec


@needs_fts5
def test_hybrid_search_matches_fusing_full_results(keyword_vault, note_length):
    root, keyword = keyword_vault
    index = SemanticIndex(
        root / ".palace" / "semantic_index", embedding_backend=_BagOfWords(), keyword_index=keyword
    )
    index.index_vault(root)
    for query in QUERIES[:4]:
        for limit in (3, 10):
            depth = max(limit, 20)
            want = fuse_rrf(
                index._semantic_ranked(query, depth), keyword.search(query, depth), limit
            )
            got = index.search(query, limit)
            assert [(r.path, r.score, r.match_type, r.content_excerpt) for r in got] == [
                (r.path, r.score, r.match_type, r.content_excerpt) for r in want
            ]
            assert all(r.content_excerpt for r in got)


# ---------------------------------------------------------------------------
# The MCP server checks a vault for outside edits once per interval, not per call
# ---------------------------------------------------------------------------


@pytest.fixture
def mcp_vault(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("OMPA_VAULT_PATH", str(vault))
    # An hour, so that only what a test does to the clock causes a walk.  With
    # ompa[semantic] installed the first search loads the model, which can
    # take longer than the default second.
    monkeypatch.setenv("OMPA_INDEX_REFRESH_SECONDS", "3600")
    monkeypatch.delenv("OMPA_PRINCIPAL", raising=False)
    mcp_server._REFRESH_CLOCK.clear()
    (vault / "seed.md").write_text("The launch codename is heliotrope.", encoding="utf-8")
    scans = []
    import ompa.core as core

    real_scan = core.scan_vault
    monkeypatch.setattr(core, "scan_vault", lambda *a, **k: scans.append(1) or real_scan(*a, **k))
    yield vault, scans
    mcp_server._REFRESH_CLOCK.clear()


def _search(vault, query):
    result = mcp_server.handle_call_tool("ao_search", {"vault_path": str(vault), "query": query})
    assert "error" not in result, result
    return [Path(r["path"]).name for r in result["results"]]


@needs_fts5
def test_mcp_search_walks_the_vault_once_per_interval(mcp_vault):
    vault, scans = mcp_vault
    assert _search(vault, "heliotrope") == ["seed.md"]
    assert len(scans) == 1
    for _ in range(5):
        assert _search(vault, "heliotrope") == ["seed.md"]
    assert len(scans) == 1  # five more calls, no further walk

    mcp_server._REFRESH_CLOCK.clear()  # as if the interval had passed
    _search(vault, "heliotrope")
    assert len(scans) == 2


@needs_fts5
def test_mcp_search_sees_an_outside_edit_after_the_interval(mcp_vault):
    vault, _ = mcp_vault
    assert _search(vault, "heliotrope") == ["seed.md"]
    (vault / "outside.md").write_text("Edited in another editor: marmalade.", encoding="utf-8")
    # Membership, not equality: with ompa[semantic] installed a search also
    # returns the nearest notes by meaning, so seed.md can appear for any query.
    assert "outside.md" not in _search(vault, "marmalade")  # inside the interval
    key = next(iter(mcp_server._REFRESH_CLOCK))
    mcp_server._REFRESH_CLOCK[key] -= 7200  # two hours pass
    assert _search(vault, "marmalade")[0] == "outside.md"


@needs_fts5
def test_mcp_write_is_searchable_at_once(mcp_vault):
    vault, scans = mcp_vault
    _search(vault, "heliotrope")
    written = mcp_server.handle_call_tool(
        "ao_write",
        {"vault_path": str(vault), "content": "Tangerine is the fallback codename."},
    )
    assert "error" not in written, written
    assert _search(vault, "tangerine")[0] == "Tangerine-is-the-fallback-codename.md"
    # Keyword-only, the write itself put the note in the index: no new walk.
    # With a semantic index on disk the write could not embed the note (the
    # write tool does not load the model), so the search checks the vault.
    embedded = (vault / ".palace" / "semantic_index" / "index_meta.json").exists()
    assert len(scans) == (2 if embedded else 1)
    assert _search(vault, "tangerine")[0] == "Tangerine-is-the-fallback-codename.md"
    assert len(scans) == (2 if embedded else 1)  # and only once


@needs_fts5
def test_mcp_refresh_interval_setting(mcp_vault, monkeypatch):
    vault, scans = mcp_vault
    monkeypatch.setenv("OMPA_INDEX_REFRESH_SECONDS", "0")
    for _ in range(3):
        _search(vault, "heliotrope")
    assert len(scans) == 3  # 0 = check on every call, the behaviour before 1.2.1
    for raw, seconds in (("2.5", 2.5), ("-4", 0.0), ("soon", 1.0), ("", 1.0)):
        monkeypatch.setenv("OMPA_INDEX_REFRESH_SECONDS", raw)
        assert mcp_server._refresh_interval() == seconds
    monkeypatch.delenv("OMPA_INDEX_REFRESH_SECONDS")
    assert mcp_server._refresh_interval() == 1.0


@needs_fts5
def test_mcp_clock_holds_times_only_and_is_per_vault(mcp_vault, tmp_path, monkeypatch):
    vault, scans = mcp_vault
    other = tmp_path / "other"
    other.mkdir()
    (other / "n.md").write_text("A second vault about heliotrope.", encoding="utf-8")
    _search(vault, "heliotrope")
    assert _search(other, "heliotrope") == ["n.md"]  # its first search still walks it
    assert len(scans) == 2
    assert len(mcp_server._REFRESH_CLOCK) == 2
    for key, value in mcp_server._REFRESH_CLOCK.items():
        assert isinstance(value, float)
        assert all(isinstance(part, str) for part in key)


@needs_fts5
def test_library_instances_keep_their_own_clock(tmp_path, monkeypatch):
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "a.md").write_text("Alpha note about heliotrope.", encoding="utf-8")
    import ompa.core as core

    scans = []
    real_scan = core.scan_vault
    monkeypatch.setattr(core, "scan_vault", lambda *a, **k: scans.append(1) or real_scan(*a, **k))
    first = Ompa(vault_path=vault, enable_semantic=False)
    first.search("heliotrope")
    first.search("heliotrope")
    assert len(scans) == 1
    (vault / "b.md").write_text("Written outside OMPA: marmalade.", encoding="utf-8")
    second = Ompa(vault_path=vault, enable_semantic=False)
    assert [Path(r.path).name for r in second.search("marmalade")] == ["b.md"]
    assert len(scans) == 2  # a new instance checks on its first search, as before


# ---------------------------------------------------------------------------
# A shared clock must not hide what OMPA itself wrote from the semantic index
# ---------------------------------------------------------------------------
#
# The MCP server builds one Ompa per call and its write tool does not load the
# embedding model, so a write cannot update the semantic index in place.  Before
# the shared clock every search checked the vault and caught up.  With it, a
# search inside the interval would rank the new note by keyword alone, keep an
# edited note's old embedding, and still return a deleted note.


def _instance(vault, clock, semantic):
    return Ompa(
        vault_path=vault,
        enable_semantic=semantic,
        embedding_backend=_BagOfWords() if semantic else None,
        refresh_clock=clock,
        index_refresh_interval=3600,
    )


@pytest.fixture
def embedded_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "brain").mkdir(parents=True)
    (vault / "brain" / "seed.md").write_text("The launch codename is heliotrope.", encoding="utf-8")
    clock: dict = {}
    _instance(vault, clock, semantic=True).search("heliotrope")  # builds both indexes
    assert len(clock) == 1
    return vault, clock


def _indexed(vault, clock) -> set[str]:
    searcher = _instance(vault, clock, semantic=True)
    searcher.search("anything")
    return {Path(p).name for p in searcher.semantic.file_mtimes}


@needs_fts5
def test_a_write_behind_the_semantic_index_is_embedded_by_the_next_search(embedded_vault):
    vault, clock = embedded_vault
    _instance(vault, clock, semantic=False).write(
        "Tangerine is the fallback codename.", file_path="brain/tangerine.md"
    )
    assert clock == {}  # the next search will check this vault
    searcher = _instance(vault, clock, semantic=True)
    results = searcher.search("tangerine fallback codename")
    assert Path(results[0].path).name == "tangerine.md"
    assert {Path(p).name for p in searcher.semantic.file_mtimes} == {"seed.md", "tangerine.md"}
    assert len(clock) == 1  # checked, and the clock runs again


@needs_fts5
def test_an_edit_behind_the_semantic_index_replaces_the_old_excerpt(embedded_vault):
    vault, clock = embedded_vault
    _instance(vault, clock, semantic=False).write(
        "The launch codename is now marigold.", file_path="brain/seed.md"
    )
    assert clock == {}
    searcher = _instance(vault, clock, semantic=True)
    searcher.search("codename")
    excerpts = [c["text"] for c in searcher.semantic.chunks]
    assert any("marigold" in text for text in excerpts)
    assert not any("heliotrope" in text for text in excerpts)


@needs_fts5
def test_a_delete_behind_the_semantic_index_is_not_returned(embedded_vault):
    vault, clock = embedded_vault
    writer = _instance(vault, clock, semantic=False)
    writer.write("Tangerine is the fallback codename.", file_path="brain/tangerine.md")
    assert _indexed(vault, clock) == {"seed.md", "tangerine.md"}
    assert len(clock) == 1

    assert _instance(vault, clock, semantic=False).delete_note("brain/tangerine.md")["success"]
    assert clock == {}
    searcher = _instance(vault, clock, semantic=True)
    assert [Path(r.path).name for r in searcher.search("tangerine fallback codename")] == [
        "seed.md"
    ]
    assert {Path(p).name for p in searcher.semantic.file_mtimes} == {"seed.md"}


@needs_fts5
def test_a_loaded_semantic_index_follows_the_write_without_a_walk(embedded_vault):
    vault, clock = embedded_vault
    resident = _instance(vault, clock, semantic=True)
    resident.search("heliotrope")
    before = dict(clock)
    resident.write("Tangerine is the fallback codename.", file_path="brain/tangerine.md")
    assert clock == before  # updated in place: nothing to catch up
    assert Path(resident.search("tangerine fallback codename")[0].path).name == "tangerine.md"


@needs_fts5
def test_a_keyword_only_vault_needs_no_walk_after_a_write(tmp_path):
    vault = tmp_path / "vault"
    (vault / "brain").mkdir(parents=True)
    (vault / "brain" / "seed.md").write_text("The launch codename is heliotrope.", encoding="utf-8")
    clock: dict = {}
    _instance(vault, clock, semantic=False).search("heliotrope")
    before = dict(clock)
    writer = _instance(vault, clock, semantic=False)
    writer.write("Tangerine is the fallback codename.", file_path="brain/tangerine.md")
    writer.delete_note("brain/seed.md")
    assert clock == before  # no semantic index on disk: the keyword index was updated in place
    found = _instance(vault, clock, semantic=False).search("codename")
    assert [Path(r.path).name for r in found] == ["tangerine.md"]


@needs_fts5
def test_only_the_written_vault_is_checked_again(embedded_vault, tmp_path):
    vault, clock = embedded_vault
    other = tmp_path / "other"
    other.mkdir()
    (other / "n.md").write_text("A second vault.", encoding="utf-8")
    _instance(other, clock, semantic=True).search("second")
    assert len(clock) == 2
    _instance(vault, clock, semantic=False).write("Tangerine.", file_path="brain/t.md")
    assert [Path(key[1]).parent.parent.name for key in clock] == ["other"]


# ---------------------------------------------------------------------------
# A loaded semantic index kept between instances is only reused while it is right
# ---------------------------------------------------------------------------


def _cached(vault, cache, clock=None, backend=None):
    return Ompa(
        vault_path=vault,
        enable_semantic=True,
        embedding_backend=backend if backend is not None else _SHARED_BACKEND,
        refresh_clock=clock if clock is not None else {},
        semantic_cache=cache,
        index_refresh_interval=3600,
    )


_SHARED_BACKEND = _BagOfWords()


@pytest.fixture
def cached_vault(tmp_path):
    vault = tmp_path / "vault"
    (vault / "brain").mkdir(parents=True)
    for name, text in [
        ("seed", "The launch codename is heliotrope."),
        ("ada", "Ada Lovelace reviewed the analytical engine notes."),
        ("plan", "The migration plan moves the ledger to Postgres."),
    ]:
        (vault / "brain" / f"{name}.md").write_text(text, encoding="utf-8")
    cache: dict = {}
    first = _cached(vault, cache)
    first.search("heliotrope")
    return vault, cache, first.semantic


def _names(results):
    return [Path(r.path).name for r in results]


@needs_fts5
def test_the_next_instance_reuses_the_loaded_index(cached_vault, monkeypatch):
    vault, cache, loaded = cached_vault
    assert list(cache.values()) == [loaded]
    monkeypatch.setattr(
        SemanticIndex, "load_index", lambda self: pytest.fail("the index was read again")
    )
    second = _cached(vault, cache)
    assert second.semantic is loaded
    assert loaded.keyword_index is second.keyword  # searches use this instance's connection
    assert _names(second.search("heliotrope codename"))[0] == "seed.md"


@needs_fts5
def test_an_instance_without_a_cache_loads_its_own(cached_vault):
    vault, cache, loaded = cached_vault
    alone = Ompa(vault_path=vault, enable_semantic=True, embedding_backend=_SHARED_BACKEND)
    assert alone.semantic is not loaded
    assert list(cache.values()) == [loaded]


@needs_fts5
def test_an_index_written_by_someone_else_is_loaded_again(cached_vault):
    """Another process, or another SemanticIndex over the same folder."""
    vault, cache, loaded = cached_vault
    other = SemanticIndex(vault / ".palace" / "semantic_index", embedding_backend=_SHARED_BACKEND)
    assert other.load_index()
    (vault / "brain" / "new.md").write_text("Marmalade is the new codename.", encoding="utf-8")
    other.refresh(vault)  # indexes new.md and saves
    assert not loaded.in_step_with_disk()

    second = _cached(vault, cache)
    assert second.semantic is not loaded
    assert {Path(p).name for p in second.semantic.file_mtimes} == {
        "seed.md",
        "ada.md",
        "plan.md",
        "new.md",
    }
    assert list(cache.values()) == [second.semantic]


@needs_fts5
def test_an_index_with_unsaved_changes_is_not_reused(cached_vault):
    vault, cache, loaded = cached_vault
    (vault / "brain" / "draft.md").write_text("Unsaved marmalade.", encoding="utf-8")
    loaded.update_file(vault / "brain" / "draft.md", save=False)
    assert not loaded.in_step_with_disk()
    second = _cached(vault, cache)
    assert second.semantic is not loaded
    # what it loaded is what was on disk; the next check of the vault adds the note
    assert "draft.md" not in {Path(p).name for p in second.semantic.file_mtimes}
    assert "draft.md" in _names(second.search("unsaved marmalade"))


@needs_fts5
def test_a_different_embedding_backend_does_not_reuse_the_index(cached_vault):
    vault, cache, loaded = cached_vault
    second = _cached(vault, cache, backend=_BagOfWords())
    assert second.semantic is not loaded


@needs_fts5
def test_in_step_with_disk(tmp_path):
    index = SemanticIndex(tmp_path / "idx", embedding_backend=_SHARED_BACKEND)
    assert not index.in_step_with_disk()  # nothing loaded or saved yet
    note = tmp_path / "a.md"
    note.write_text("Alpha heliotrope.", encoding="utf-8")
    index.update_file(note, save=False)
    assert not index.in_step_with_disk()  # unsaved
    index.save_index()
    assert index.in_step_with_disk()
    reader = SemanticIndex(tmp_path / "idx", embedding_backend=_SHARED_BACKEND)
    assert reader.load_index() and reader.in_step_with_disk()
    (tmp_path / "idx" / "index_vectors.f16").unlink()
    assert not reader.in_step_with_disk()  # a file is gone
    index.save_index()
    index.clear()
    assert not index.in_step_with_disk()


@needs_fts5
def test_forget_leaves_no_copy_of_the_subject_in_the_cache(cached_vault):
    vault, cache, loaded = cached_vault
    assert any("Lovelace" in chunk["text"] for chunk in loaded.chunks)
    # The MCP forget tool runs without the embedding model loaded.
    eraser = Ompa(vault_path=vault, enable_semantic=False, semantic_cache=cache, refresh_clock={})
    eraser.forget("Ada Lovelace")
    assert cache == {}

    second = _cached(vault, cache)
    assert second.semantic is not loaded
    assert not any("Lovelace" in chunk["text"] for chunk in second.semantic.chunks)
    assert not any("Lovelace" in r.content_excerpt for r in second.search("Lovelace engine"))


@needs_fts5
def test_forget_dry_run_keeps_the_cache(cached_vault):
    vault, cache, loaded = cached_vault
    Ompa(vault_path=vault, enable_semantic=False, semantic_cache=cache).forget(
        "Ada Lovelace", dry_run=True
    )
    assert list(cache.values()) == [loaded]


@needs_fts5
def test_delete_and_retention_drop_the_cached_index(cached_vault):
    vault, cache, loaded = cached_vault
    writer = Ompa(vault_path=vault, enable_semantic=False, semantic_cache=cache, refresh_clock={})
    assert writer.delete_note("brain/plan.md")["success"]
    assert cache == {}
    second = _cached(vault, cache)
    assert "plan.md" not in _names(second.search("migration plan ledger"))
    assert len(cache) == 1

    admin = Ompa(
        vault_path=vault,
        enable_semantic=False,
        semantic_cache=cache,
        refresh_clock={},
        audit=True,
    )
    admin.add_retention_rule(30, all=True)
    later = datetime.now(timezone.utc) + timedelta(days=31)
    assert admin.apply_retention(dry_run=True, now=later)[0]["notes"]
    assert len(cache) == 1  # a dry run changes nothing
    admin.apply_retention(now=later)
    assert cache == {}
    assert _names(_cached(vault, cache).search("heliotrope codename")) == []


@needs_fts5
def test_the_cache_holds_a_bounded_number_of_vaults(tmp_path):
    cache: dict = {}
    limit = Ompa.SEMANTIC_CACHE_VAULTS
    vaults = []
    for i in range(limit + 2):
        vault = tmp_path / f"v{i}"
        vault.mkdir()
        (vault / "n.md").write_text(f"Vault number {i} heliotrope.", encoding="utf-8")
        _cached(vault, cache).search("heliotrope")
        vaults.append(vault)
    assert len(cache) == limit
    kept = [Path(key).parent.parent.name for key in cache]
    assert kept == [v.name for v in vaults[-limit:]]  # the earliest two were dropped


@needs_fts5
def test_cached_and_uncached_instances_return_the_same_results(tmp_path):
    """The same sequence of writes, edits, outside edits and deletes, searched
    after each step by a new instance, with and without the shared cache."""

    def run(vault, cache):
        vault.mkdir()
        (vault / "brain").mkdir()
        clock: dict = {}
        seen = []

        def step(action=None):
            if action is not None:
                action()
                clock.clear()  # the next search checks the vault
            ao = Ompa(
                vault_path=vault,
                enable_semantic=True,
                embedding_backend=_SHARED_BACKEND,
                refresh_clock=clock,
                semantic_cache=cache,
                index_refresh_interval=3600,
            )
            for query in ("heliotrope codename", "ledger migration", "marmalade"):
                seen.append(
                    [(Path(r.path).name, r.score, r.content_excerpt) for r in ao.search(query)]
                )

        writer = lambda: Ompa(  # noqa: E731
            vault_path=vault, enable_semantic=False, refresh_clock=clock, semantic_cache=cache
        )
        step(lambda: writer().write("The launch codename is heliotrope.", file_path="brain/a.md"))
        step(lambda: writer().write("The ledger migration is planned.", file_path="brain/b.md"))
        step()  # nothing changed: the cached index is reused as it is
        step(lambda: writer().write("The codename is now marmalade.", file_path="brain/a.md"))
        step(lambda: (vault / "brain" / "c.md").write_text("Outside edit: marmalade ledger."))
        step(lambda: writer().delete_note("brain/b.md"))
        step(lambda: (vault / "brain" / "c.md").unlink())
        return seen

    assert run(tmp_path / "with", {}) == run(tmp_path / "without", None)


# ---------------------------------------------------------------------------
# Ranking by the rare words first returns what ranking every match did
# ---------------------------------------------------------------------------
#
# On an index of 2,000 notes or more, a query with words found in most notes is
# ranked by its rarer words, and only the rows that could still reach the
# results are scored by the whole query.  The reference is the statement that
# scores every match, reached by raising the size from which the shortcut is
# used.  Paths, scores and excerpts must be equal, not close.

import random  # noqa: E402

import ompa.keyword as keyword_module  # noqa: E402

COMMON_WORDS = "the of and to a in is it that for on with as was at by".split()  # noqa: SIM905


def _prose_index(root, notes, seed, same_length):
    """Notes that are about half words found in most notes, like prose."""
    rng = random.Random(seed)
    root.mkdir(parents=True)
    for i in range(notes):
        length = 40 if same_length else rng.randint(4, 120)
        words = []
        for _ in range(length):
            if rng.random() < 0.55:
                words.append(rng.choice(COMMON_WORDS))
            else:
                words.append(f"term{min(int(rng.paretovariate(1.1)), 300)}")
        (root / f"n{i:04d}.md").write_text(" ".join(words) + f" only{i}", encoding="utf-8")
    index = KeywordIndex(root / ".palace" / "keyword_index.sqlite3")
    index.refresh(root)
    return index


# term1..term4 are in most notes, term5 in about half, term50 in about one in 200.
TERM_NUMBERS = [1, 3, 5, 6, 7, 8, 9, 10, 12, 15, 20, 30, 50, 100, 250]


def _random_query(rng, notes):
    words = rng.sample(COMMON_WORDS, rng.randint(0, 6))
    words += [f"term{rng.choice(TERM_NUMBERS)}" for _ in range(rng.randint(0, 4))]
    if rng.random() < 0.4:
        words.append(f"only{rng.randrange(notes)}")
    if rng.random() < 0.2:
        words.append("absentword")
    rng.shuffle(words)
    return " ".join(words) or "the term1"


def _as_tuples(results):
    return [(r.path, r.score, r.content_excerpt, r.match_type) for r in results]


@pytest.fixture
def shortcut(monkeypatch):
    """Switch between the shortcut (from any size) and scoring every match, and
    count the searches the shortcut answered."""
    taken = []
    real = KeywordIndex._ranked_by_rare_words

    def counted(self, conn, words, fts_q, limit):
        ranked = real(self, conn, words, fts_q, limit)
        if ranked is not None:
            taken.append(fts_q)
        return ranked

    monkeypatch.setattr(KeywordIndex, "_ranked_by_rare_words", counted)

    class Switch:
        answered = taken

        @staticmethod
        def on():
            monkeypatch.setattr(keyword_module, "_PRUNE_MIN_NOTES", 0)

        @staticmethod
        def off():
            monkeypatch.setattr(keyword_module, "_PRUNE_MIN_NOTES", 10**12)

    Switch.on()
    return Switch


def _both_ways(index, shortcut, query, limit, excerpts=True):
    shortcut.on()
    fast = _as_tuples(index.search(query, limit, excerpts=excerpts))
    shortcut.off()
    slow = _as_tuples(index.search(query, limit, excerpts=excerpts))
    shortcut.on()
    return fast, slow


@needs_fts5
@pytest.mark.parametrize("same_length", [False, True], ids=["varied lengths", "equal lengths"])
@pytest.mark.parametrize("seed", [1, 2, 3])
def test_rare_words_first_equals_scoring_every_match(tmp_path, shortcut, seed, same_length):
    """Equal lengths make many rows tie on the rare words, so the common words
    decide their order: the case the shortcut has to get exactly right."""
    notes = 400
    index = _prose_index(tmp_path / "v", notes, seed, same_length)
    rng = random.Random(seed * 100)
    checked = 0
    for _ in range(60):
        query = _random_query(rng, notes)
        for limit in (1, 5, 20, 100):
            fast, slow = _both_ways(index, shortcut, query, limit, excerpts=limit != 20)
            assert fast == slow, (query, limit)
            checked += 1
    # the comparison means something only if the shortcut answered a good share
    assert len(shortcut.answered) > checked // 4
    index.close()


@needs_fts5
@pytest.mark.parametrize("notes", [200, 201])
def test_a_word_in_exactly_half_the_notes(tmp_path, shortcut, notes):
    """bm25() floors the IDF of a word in at least half the rows.  Half of an
    odd count rounds up."""
    root = tmp_path / "v"
    root.mkdir()
    half = (notes + 1) // 2
    for i in range(notes):
        words = ["the"] * 3 + [f"filler{i % 7}"] * (i % 5 + 1)
        words += ["exactly"] * (i < half) + ["under"] * (i < half - 1) + ["over"] * (i <= half)
        words += ["rare"] * (i % 11 == 0) + [f"only{i}"]
        (root / f"n{i:03d}.md").write_text(" ".join(words), encoding="utf-8")
    index = KeywordIndex(root / ".palace" / "keyword_index.sqlite3")
    index.refresh(root)
    conn = index._conn
    assert index._common_words(conn, ["the", "exactly", "over", "under", "rare"], half) == {
        "the",
        "exactly",
        "over",
    }
    for query in ("exactly rare", "over rare", "under rare", "exactly over under rare the"):
        before = len(shortcut.answered)
        for limit in (1, 5, 20):
            fast, slow = _both_ways(index, shortcut, query, limit)
            assert fast == slow, (query, limit)
        if query != "under rare":  # no common word in that one
            assert len(shortcut.answered) > before, query
    # what a search itself decided, with its own count of the rows
    assert index._common == {
        "the": True,
        "exactly": True,
        "over": True,
        "under": False,
        "rare": False,
    }
    index.close()


@needs_fts5
def test_the_shortcut_stands_aside_when_it_cannot_be_exact(tmp_path, shortcut, monkeypatch):
    index = _prose_index(tmp_path / "v", 300, seed=7, same_length=True)
    query = "the of term8 term10 term12 and"

    def answered(limit=5):
        before = len(shortcut.answered)
        fast, slow = _both_ways(index, shortcut, query, limit)
        assert fast == slow
        return len(shortcut.answered) > before

    assert answered()
    # more rows left to score than it scores
    monkeypatch.setattr(keyword_module, "_PRUNE_MAX_CANDIDATES", 2)
    assert not answered()
    monkeypatch.setattr(keyword_module, "_PRUNE_MAX_CANDIDATES", 900)
    # scores that do not keep the bound it relies on
    monkeypatch.setattr(keyword_module, "_COMMON_WORD_BOUND", 1e-13)
    assert not answered()
    # common words that could outweigh the rare ones: rows matching only
    # common words might then reach the results
    monkeypatch.setattr(keyword_module, "_COMMON_WORD_BOUND", 10.0)
    assert not answered()
    monkeypatch.setattr(keyword_module, "_COMMON_WORD_BOUND", 1e-6 * 2.2)
    assert answered()
    # fewer rows match a rare word than were asked for
    before = len(shortcut.answered)
    fast, slow = _both_ways(index, shortcut, "the of only5 and", 5)
    assert fast == slow and len(fast) == 5 and len(shortcut.answered) == before
    # nothing but common words, nothing but rare ones, a single word
    for other in ("the of and", "term10 term12", "the"):
        before = len(shortcut.answered)
        fast, slow = _both_ways(index, shortcut, other, 5)
        assert fast == slow and len(shortcut.answered) == before, other
    index.close()


@needs_fts5
def test_a_small_index_scores_every_match(tmp_path, shortcut, monkeypatch):
    index = _prose_index(tmp_path / "v", 300, seed=4, same_length=False)
    query = "the of term8 term10 term12 and"
    assert index.search(query, 5)
    assert len(shortcut.answered) == 1  # it would answer this one, size permitting
    monkeypatch.setattr(keyword_module, "_PRUNE_MIN_NOTES", 2000)  # the shipped value
    assert index.search(query, 5)
    assert len(shortcut.answered) == 1
    index.close()


@needs_fts5
def test_which_words_are_common_is_asked_once_per_state_of_the_index(tmp_path, shortcut):
    """It is remembered while the index is unchanged, and asked again after a
    write by this index object or by another one."""
    root = tmp_path / "v"
    root.mkdir()
    for i in range(100):
        body = (
            "the " * 3 + ("pivot " if i < 60 else "") + ("rare " if i % 9 == 0 else "") + f"only{i}"
        )
        (root / f"n{i:03d}.md").write_text(body, encoding="utf-8")
    index = KeywordIndex(root / ".palace" / "keyword_index.sqlite3")
    index.refresh(root)

    counts = []
    conn = index._conn
    real_execute = conn.execute

    class Counting:
        def __getattr__(self, name):
            return getattr(conn, name)

        def execute(self, sql, *args):
            if sql is keyword_module._AT_LEAST_SQL:
                counts.append(args[0][0])
            return real_execute(sql, *args)

    index._conn = Counting()
    query = "the pivot rare"
    fast, slow = _both_ways(index, shortcut, query, 5)
    assert fast == slow
    assert sorted(counts) == ['"pivot"', '"rare"', '"the"']
    for _ in range(3):
        index.search(query, 5)
    assert len(counts) == 3  # remembered

    # "pivot" was in 60 of 100 notes.  Remove 30 of those: now 30 of 70, rare.
    for i in range(30):
        (root / f"n{i:03d}.md").unlink()
    index.refresh(root)
    fast, slow = _both_ways(index, shortcut, query, 5)
    assert fast == slow
    assert len(counts) == 6  # asked again after this object's own write
    assert index._common == {"the": True, "pivot": False, "rare": False}

    # Another index object over the same file adds notes: 30 + 45 of 115, common.
    other = KeywordIndex(root / ".palace" / "keyword_index.sqlite3")
    for i in range(100, 145):
        (root / f"n{i:03d}.md").write_text("the the pivot " + f"only{i}", encoding="utf-8")
    other.refresh(root)
    other.close()
    fast, slow = _both_ways(index, shortcut, query, 5)
    assert fast == slow
    assert len(counts) == 9
    assert index._common == {"the": True, "pivot": True, "rare": False}
    index._conn = conn
    index.close()


@needs_fts5
def test_hybrid_search_is_unchanged_by_the_shortcut(tmp_path, shortcut):
    root = tmp_path / "v"
    keyword = _prose_index(root, 300, seed=9, same_length=True)
    index = SemanticIndex(
        root / ".palace" / "semantic_index", embedding_backend=_BagOfWords(), keyword_index=keyword
    )
    index.index_vault(root)
    rng = random.Random(90)
    queries = ["the of term8 term9 term10 and", "what is the term7 for term12"]
    queries += [_random_query(rng, 300) for _ in range(25)]
    for query in queries:
        for limit in (3, 10):
            shortcut.on()
            fast = _as_tuples(index.search(query, limit))
            shortcut.off()
            slow = _as_tuples(index.search(query, limit))
            assert fast == slow, (query, limit)
    assert shortcut.answered
    keyword.close()


TEN_COMMON = COMMON_WORDS[:10]


def _index_where_common_words_decide(root, favoured):
    """Thirty short notes that make ten words common, then the notes given.

    ``favoured`` maps a file name to (occurrences of "zebra", occurrences of
    each common word).  Every such note is padded to the same length, so only
    those two numbers separate their scores.
    """
    root.mkdir(parents=True)
    for i in range(30):
        (root / f"a{i:02d}.md").write_text(" ".join(TEN_COMMON) + f" only{i}", encoding="utf-8")
    length = max(z + 10 * c for z, c in favoured.values()) + 50
    for name, (zebras, commons) in favoured.items():
        words = ["zebra"] * zebras + TEN_COMMON * commons
        words += ["pad"] * (length - len(words))
        (root / name).write_text(" ".join(words), encoding="utf-8")
    index = KeywordIndex(root / ".palace" / "keyword_index.sqlite3")
    index.refresh(root)
    return index


@needs_fts5
def test_a_row_just_behind_on_rare_words_can_win_on_common_ones(tmp_path, shortcut):
    """The reason rows within the bound of the cut are scored in full.

    One more "zebra" among 5,000 puts b1 ahead on the rare word by a few
    millionths.  b2 has the common words and b1 has none, which is worth more
    than that.  Ranking every match puts b2 first, so the shortcut must too.
    """
    index = _index_where_common_words_decide(
        tmp_path / "v", {"b1.md": (5001, 0), "b2.md": (5000, 50)}
    )
    query = "zebra " + " ".join(TEN_COMMON)
    by_rare = index.search("zebra", 2)
    assert [Path(r.path).name for r in by_rare] == ["b1.md", "b2.md"]
    gap = by_rare[0].score - by_rare[1].score
    assert 0 < gap < keyword_module._COMMON_WORD_BOUND * 10

    fast, slow = _both_ways(index, shortcut, query, 1)
    assert Path(slow[0][0]).name == "b2.md"
    assert fast == slow
    assert len(shortcut.answered) == 1
    fast, slow = _both_ways(index, shortcut, query, 2)
    assert fast == slow and [Path(r[0]).name for r in fast] == ["b2.md", "b1.md"]
    index.close()


@needs_fts5
def test_a_ranking_cut_off_among_ties_is_not_used(tmp_path, shortcut, monkeypatch):
    """Ten notes tie on the rare word and the last has the common words, so it
    ranks first.  With room for two rows, the ranking by rare words stops at
    the first three, all still in the running, and the last note is not among
    them: the shortcut has to stand aside."""
    notes = {f"c{i}.md": (20, 0) for i in range(9)}
    notes["c9.md"] = (20, 5)
    index = _index_where_common_words_decide(tmp_path / "v", notes)
    query = "zebra " + " ".join(TEN_COMMON)

    fast, slow = _both_ways(index, shortcut, query, 1)
    assert Path(slow[0][0]).name == "c9.md" and fast == slow
    assert len(shortcut.answered) == 1  # ten rows in the running: all scored

    monkeypatch.setattr(keyword_module, "_PRUNE_MAX_CANDIDATES", 2)
    fast, slow = _both_ways(index, shortcut, query, 1)
    assert Path(fast[0][0]).name == "c9.md" and fast == slow
    assert len(shortcut.answered) == 1  # not this time
    index.close()
