"""The fast paths return what the slow ones did.

1.2.1 makes vault walks, vault statistics, keyword search and the MCP server's
staleness check cheaper.  Each test here pins the new code to the behaviour of
the code it replaced, written out below as the reference.
"""

import os
import sqlite3
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
    monkeypatch.delenv("OMPA_INDEX_REFRESH_SECONDS", raising=False)
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
    assert _search(vault, "marmalade") == []  # inside the interval: not looked for yet
    key = next(iter(mcp_server._REFRESH_CLOCK))
    mcp_server._REFRESH_CLOCK[key] -= 5  # five seconds pass
    assert _search(vault, "marmalade") == ["outside.md"]


@needs_fts5
def test_mcp_write_is_searchable_at_once(mcp_vault):
    vault, scans = mcp_vault
    _search(vault, "heliotrope")
    written = mcp_server.handle_call_tool(
        "ao_write",
        {"vault_path": str(vault), "content": "Tangerine is the fallback codename."},
    )
    assert "error" not in written, written
    assert len(_search(vault, "tangerine")) == 1
    assert len(scans) == 1  # found through the write, not through a new walk


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
