"""Regression tests for vault confinement, note naming and folder exclusions."""

import os
from pathlib import Path

import pytest

from ompa import Ompa, mcp_server
from ompa.vault import is_excluded

# ---------------------------------------------------------------------------
# MCP server: the client cannot choose an arbitrary directory as the vault
# ---------------------------------------------------------------------------


@pytest.fixture
def pinned(monkeypatch, tmp_path):
    """A server configured for exactly one vault, with no extra roots."""
    vault = tmp_path / "vault"
    vault.mkdir()
    monkeypatch.setenv("OMPA_VAULT_PATH", str(vault))
    monkeypatch.delenv("OMPA_VAULT_ROOTS", raising=False)
    monkeypatch.delenv("OMPA_PRINCIPAL", raising=False)
    return vault


def test_default_vault_is_usable_when_named_explicitly(pinned):
    result = mcp_server.handle_call_tool("ao_kg_stats", {"vault_path": str(pinned)})
    assert "error" not in result


def test_write_outside_the_configured_vault_is_refused(pinned, tmp_path):
    outside = tmp_path / "home" / ".ssh"
    outside.mkdir(parents=True)
    result = mcp_server.handle_call_tool(
        "ao_write",
        {
            "content": "ssh-ed25519 AAAA attacker",
            "file_path": "note.md",
            "vault_path": str(outside),
        },
    )
    assert result["error"] == "Invalid vault_path"
    assert "OMPA_VAULT_ROOTS" in result["detail"]
    # nothing was created: no note, no vault skeleton, no .palace
    assert list(outside.iterdir()) == []


def test_search_outside_the_configured_vault_is_refused(pinned, tmp_path):
    other = tmp_path / "other_project"
    other.mkdir()
    (other / "NOTES.md").write_text("database password is hunter2", encoding="utf-8")
    result = mcp_server.handle_call_tool(
        "ao_search", {"query": "database password", "vault_path": str(other)}
    )
    assert result["error"] == "Invalid vault_path"
    assert not (other / ".palace").exists()


@pytest.mark.parametrize("tool", ["ao_init", "ao_session_start", "ao_sync", "ao_forget"])
def test_every_tool_is_confined(pinned, tmp_path, tool):
    other = tmp_path / "elsewhere"
    other.mkdir()
    result = mcp_server.handle_call_tool(tool, {"vault_path": str(other), "subject": "Someone"})
    assert result["error"] == "Invalid vault_path"
    assert list(other.iterdir()) == []


@pytest.mark.parametrize("root", ["/", "//", "/.", "/./"])
def test_filesystem_root_is_never_a_vault(monkeypatch, root):
    # even when the operator's own configuration points at it
    monkeypatch.setenv("OMPA_VAULT_PATH", root)
    monkeypatch.setenv("OMPA_VAULT_ROOTS", root)
    assert mcp_server.handle_call_tool("ao_kg_stats", {"vault_path": root}) == {
        "error": "Invalid vault_path"
    }


def test_symlink_out_of_the_vault_root_is_refused(pinned, tmp_path, monkeypatch):
    roots = tmp_path / "vaults"
    roots.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    link = roots / "looks-fine"
    try:
        link.symlink_to(secret, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks unavailable")
    monkeypatch.setenv("OMPA_VAULT_ROOTS", str(roots))
    result = mcp_server.handle_call_tool("ao_kg_stats", {"vault_path": str(link)})
    assert result["error"] == "Invalid vault_path"


def test_vault_roots_allow_several_vaults(pinned, tmp_path, monkeypatch):
    roots = tmp_path / "vaults"
    (roots / "team-a").mkdir(parents=True)
    monkeypatch.setenv("OMPA_VAULT_ROOTS", os.pathsep.join([str(roots), str(tmp_path / "x")]))
    ok = mcp_server.handle_call_tool("ao_kg_stats", {"vault_path": str(roots / "team-a")})
    assert "error" not in ok
    # a sibling of the root with the root's name as a prefix is not inside it
    (tmp_path / "vaults-evil").mkdir()
    bad = mcp_server.handle_call_tool("ao_kg_stats", {"vault_path": str(tmp_path / "vaults-evil")})
    assert bad["error"] == "Invalid vault_path"


def test_dual_vault_paths_are_confined(pinned, tmp_path):
    result = mcp_server.handle_call_tool(
        "ao_write",
        {
            "content": "x",
            "shared_vault_path": str(pinned),
            "personal_vault_path": str(tmp_path / "not-allowed"),
        },
    )
    assert result["error"] == "Invalid vault_path"
    assert not (tmp_path / "not-allowed").exists()


@pytest.mark.parametrize(
    "name",
    ["authorized_keys", "notes.txt", ".palace/acl.json", ".git/hooks/pre-commit", ".hidden/a.md"],
)
def test_ao_write_only_writes_markdown_notes(pinned, name):
    result = mcp_server.handle_call_tool(
        "ao_write", {"content": "x", "file_path": name, "vault_path": str(pinned)}
    )
    assert result["error"] == "Invalid file_path"
    assert not (pinned / name).exists()


def test_ao_write_still_writes_a_note(pinned):
    result = mcp_server.handle_call_tool(
        "ao_write",
        {
            "content": "Decided on Postgres.",
            "file_path": "work/active/db.md",
            "vault_path": str(pinned),
        },
    )
    assert "error" not in result
    assert (pinned / "work" / "active" / "db.md").exists()


# ---------------------------------------------------------------------------
# write(): auto-named notes never replace a different note
# ---------------------------------------------------------------------------


def test_auto_named_notes_with_the_same_opening_are_both_kept(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    first = ao.write("Summary of the conversation with Dana: she approved the Q3 budget.")
    second = ao.write("Summary of the conversation with Priya: the vendor contract is cancelled.")
    third = ao.write("Summary of the conversation with Omar: launch moved to May.")

    paths = [Path(r["path"]) for r in (first, second, third)]
    assert len(set(paths)) == 3
    assert "Dana" in paths[0].read_text(encoding="utf-8")
    assert "Priya" in paths[1].read_text(encoding="utf-8")
    assert "Omar" in paths[2].read_text(encoding="utf-8")
    assert paths[1].name.endswith("-2.md") and paths[2].name.endswith("-3.md")


def test_rewriting_identical_auto_named_content_does_not_duplicate(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    text = "Summary of the conversation with Dana: she approved the Q3 budget."
    assert ao.write(text)["path"] == ao.write(text)["path"]


def test_explicit_file_path_still_overwrites(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    ao.write("v1", file_path="work/active/spec.md")
    result = ao.write("v2", file_path="work/active/spec.md")
    assert Path(result["path"]).read_text(encoding="utf-8").strip().endswith("v2")
    assert len(list((tmp_path / "work" / "active").glob("*.md"))) == 1


# ---------------------------------------------------------------------------
# Folder exclusions match path components, not substrings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("parent", [".claude", "design-thinking", ".github", "thinking"])
def test_vault_location_does_not_hide_its_notes(tmp_path, parent):
    vault = tmp_path / parent / "vault"
    ao = Ompa(vault_path=vault, enable_semantic=False)
    ao.write("Quarterly roadmap for the Orion launch [[Orion]]", file_path="work/active/roadmap.md")

    assert [n.path.name for n in ao.vault.list_notes() if n.path.name == "roadmap.md"]
    assert any(r.path.endswith("roadmap.md") for r in ao.search("roadmap Orion"))
    assert ao.kg_populate() > 0


def test_note_named_like_an_excluded_folder_is_indexed(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    ao.write("Rethinking the auth flow for mobile", file_path="work/active/Rethinking auth.md")
    assert any(n.path.name == "Rethinking auth.md" for n in ao.vault.list_notes())
    assert ao.search("auth flow mobile")


def test_excluded_folders_inside_the_vault_stay_excluded(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    for folder in ("thinking", ".git", ".claude"):
        (tmp_path / folder).mkdir(exist_ok=True)
        (tmp_path / folder / "scratch.md").write_text("zebra quagga okapi", encoding="utf-8")
    ao.write("zebra field notes", file_path="work/active/zebra.md")

    names = {n.path.name for n in ao.vault.list_notes()}
    assert "scratch.md" not in names and "zebra.md" in names
    assert all(not r.path.endswith("scratch.md") for r in ao.search("zebra quagga okapi"))


def test_is_excluded_patterns():
    root = Path("/v")
    assert is_excluded(root / "thinking" / "a.md", root)
    assert is_excluded(root / "work" / ".git" / "a.md", root)
    assert not is_excluded(root / "work" / "thinking.md", root)
    assert not is_excluded(root / "work" / "rethinking" / "a.md", root)
    # a multi-component pattern matches a folder path
    assert is_excluded(root / "work" / "archive" / "a.md", root, ["work/archive"])
    assert not is_excluded(root / "archive" / "work" / "a.md", root, ["work/archive"])
    assert not is_excluded(root / "work" / "a.md", root, [])


# ---------------------------------------------------------------------------
# MCP protocol: every request is answered and failures are marked
# ---------------------------------------------------------------------------


def _serve(monkeypatch, requests):
    import io
    import json

    monkeypatch.setattr("sys.stdin", io.StringIO("".join(json.dumps(r) + "\n" for r in requests)))
    out = io.StringIO()
    monkeypatch.setattr("sys.stdout", out)
    mcp_server.main()
    return {r.get("id"): r for r in map(json.loads, out.getvalue().splitlines())}


def test_ping_and_unknown_methods_are_answered(monkeypatch):
    replies = _serve(
        monkeypatch,
        [
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
            {"jsonrpc": "2.0", "id": 2, "method": "resources/list"},
            {"jsonrpc": "2.0", "method": "notifications/cancelled"},  # no id: no reply
            {"jsonrpc": "2.0", "id": 3, "method": "tools/list"},
        ],
    )
    assert replies[1]["result"] == {}
    assert replies[2]["error"]["code"] == -32601
    assert len(replies[3]["result"]["tools"]) == len(mcp_server.TOOLS)
    assert set(replies) == {1, 2, 3}


def test_tool_failures_are_marked_and_explained(pinned, monkeypatch):
    def call(req_id, name, arguments):
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }

    replies = _serve(
        monkeypatch,
        [
            call(1, "ao_kg_stats", {}),
            call(2, "ao_nope", {}),
            call(3, "ao_as_of", {"when": "not a time", "entity": "x"}),
        ],
    )
    assert "isError" not in replies[1]["result"]
    assert replies[2]["result"]["isError"] is True
    assert replies[3]["result"]["isError"] is True
    assert "detail" in replies[3]["result"]["content"][0]["text"]


@pytest.mark.parametrize("tags", [["decision", " q3 "], "decision, q3"])
def test_ao_write_accepts_tags_as_list_or_string(pinned, tags):
    result = mcp_server.handle_call_tool(
        "ao_write",
        {"content": "x", "file_path": "work/active/t.md", "tags": tags, "vault_path": str(pinned)},
    )
    assert "error" not in result
    text = (pinned / "work" / "active" / "t.md").read_text(encoding="utf-8")
    assert "- decision" in text and "- q3" in text


# ---------------------------------------------------------------------------
# search(wing=..., room=...) is a filter
# ---------------------------------------------------------------------------


def test_wing_filter_returns_nothing_when_nothing_matches(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    ao.write("Payroll export schedule and salary bands", file_path="work/active/payroll.md")
    assert ao.search("payroll salary")  # found without a filter
    assert ao.search("payroll salary", wing="Orion") == []


def test_wing_filter_ignores_the_vault_location(tmp_path):
    vault = tmp_path / "Orion" / "vault"  # the wing name appears above the vault
    ao = Ompa(vault_path=vault, enable_semantic=False)
    ao.write("Payroll export schedule", file_path="work/active/payroll.md")
    ao.write("Payroll for the launch team", file_path="work/active/Orion payroll.md")
    names = [Path(r.path).name for r in ao.search("payroll", wing="Orion")]
    assert names == ["Orion payroll.md"]


def test_filter_looks_past_the_first_page_of_results(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    for i in range(12):
        ao.write(f"zebra zebra zebra migration notes {i}", file_path=f"work/active/n{i}.md")
    ao.write("one zebra mention", file_path="org/people/Kai.md")
    # the Kai note ranks below the twelve others; the filter must still find it
    assert [Path(r.path).name for r in ao.search("zebra", limit=3, wing="org")] == ["Kai.md"]


# ---------------------------------------------------------------------------
# post_tool only ingests notes that are inside a vault
# ---------------------------------------------------------------------------


def test_post_tool_ignores_files_outside_the_vault(tmp_path):
    ao = Ompa(vault_path=tmp_path / "vault", enable_semantic=False)
    outside = tmp_path / "elsewhere" / "private.md"
    outside.parent.mkdir()
    outside.write_text("My therapist is Dr. Okafor [[Health]]\n", encoding="utf-8")

    ao.post_tool("write", {"file_path": str(outside)})
    assert ao.search("therapist") == []
    assert ao.kg.query_entity("private") == []


def test_post_tool_accepts_a_vault_relative_path(tmp_path):
    ao = Ompa(vault_path=tmp_path / "vault", enable_semantic=False)
    note = tmp_path / "vault" / "work" / "active" / "auth.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("Token rotation plan [[Security]]\n", encoding="utf-8")

    ao.post_tool("write", {"file_path": "work/active/auth.md"})
    assert any(t.object == "Security" for t in ao.kg.query_entity("auth"))
    assert ao.search("token rotation")


def test_post_tool_keeps_personal_notes_out_of_the_shared_vault(tmp_path):
    shared, personal = tmp_path / "shared", tmp_path / "personal"
    ao = Ompa(shared_vault_path=shared, personal_vault_path=personal, enable_semantic=False)
    note = personal / "work" / "1-1" / "priya.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("Salary review: band 6 in March [[Compensation]]\n", encoding="utf-8")

    ao.post_tool("write", {"file_path": str(note)})
    assert ao.search("salary review", vaults=["shared"]) == []
    assert ao.kg.query_entity("priya") == []
    assert ao.search("salary review", vaults=["personal"])
    assert any(t.object == "Compensation" for t in ao.personal_kg.query_entity("priya"))


# ---------------------------------------------------------------------------
# write() keeps a note's other frontmatter; saving is atomic
# ---------------------------------------------------------------------------


def test_rewriting_a_note_keeps_its_frontmatter(tmp_path):
    import frontmatter

    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    note = tmp_path / "work" / "active" / "spec.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ndate: 2026-01-05\ndescription: Auth spec\nowner: dana\nstatus: approved\n"
        "tags: [auth]\n---\nBody v1\n",
        encoding="utf-8",
    )
    ao.write("Body v2", file_path="work/active/spec.md")
    post = frontmatter.load(note)
    assert post.content == "Body v2"
    assert post["description"] == "Auth spec" and post["owner"] == "dana"
    assert post["status"] == "approved" and post["tags"] == ["auth"]
    assert str(post["date"]) == "2026-01-05"

    ao.write("Body v3", file_path="work/active/spec.md", tags=["auth", "v3"], date="2026-02-02")
    post = frontmatter.load(note)
    assert post["tags"] == ["auth", "v3"] and str(post["date"]) == "2026-02-02"
    assert post["owner"] == "dana"


def test_failed_save_leaves_the_previous_version(tmp_path, monkeypatch):
    from ompa.vault import Note

    path = tmp_path / "note.md"
    Note(path=path, content="version one").save()

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr("ompa.vault.frontmatter.dumps", boom)
    with pytest.raises(OSError):
        Note(path=path, content="version two").save()
    assert "version one" in path.read_text(encoding="utf-8")
    assert [p.name for p in tmp_path.iterdir()] == ["note.md"]  # no temp file left behind


def test_save_through_a_symlink_keeps_the_link(tmp_path):
    from ompa.vault import Note

    real = tmp_path / "real.md"
    real.write_text("old", encoding="utf-8")
    link = tmp_path / "link.md"
    try:
        link.symlink_to(real)
    except OSError:
        pytest.skip("symlinks unavailable")
    Note(path=link, content="new").save()
    assert link.is_symlink() and "new" in real.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Export: read permission is checked before the preview; more secrets redacted
# ---------------------------------------------------------------------------


def test_export_preview_needs_read_access(tmp_path):
    from ompa.governance import AccessDeniedError

    shared, personal = tmp_path / "shared", tmp_path / "personal"
    owner = Ompa(
        shared_vault_path=shared,
        personal_vault_path=personal,
        enable_semantic=False,
        principal="alice",
    )
    owner.write("Salary review: band 6.", file_path="work/1-1/priya.md", vault="personal")
    owner.set_acl(
        {"version": 1, "owner": "alice", "teams": {}, "read": [], "write": [], "admin": ["alice"]},
        vault="personal",
    )
    other = Ompa(
        shared_vault_path=shared,
        personal_vault_path=personal,
        enable_semantic=False,
        principal="mallory",
    )
    with pytest.raises(AccessDeniedError):
        other.export_to_shared("work/1-1/priya.md")
    assert owner.export_to_shared("work/1-1/priya.md")["action"] == "preview"


@pytest.mark.parametrize(
    "secret",
    [
        "sk-" + "a" * 48,
        "sk-proj-" + "Ab3" * 16,
        "sk-ant-api03-" + "Zz9_" * 20,
        "ghp_" + "A1b2" * 9,
        "github_pat_" + "A1b2C3d4" * 4,
        "xoxb-1234567890-ABCDEFGHIJKL",
        "AKIAIOSFODNN7EXAMPLE",
        "AIza" + "Sy0aBcDeFgHiJkLmNoPqRsTuVwXyZ012345"[:35],
        "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "Authorization: Bearer abcdefghijklmnopqrstuvwx.yz012345",
        "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4eHh4In0.c2lnbmF0dXJlLXZhbHVl",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAAAA\n-----END OPENSSH PRIVATE KEY-----",
        "password: hunter2",
    ],
)
def test_sanitizer_redacts_common_credential_formats(tmp_path, secret):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    cleaned = ao._sanitize_content(f"Deploy notes.\n{secret}\nEnd.")
    value = secret.split(" = ")[-1].split("Bearer ")[-1].split(": ")[-1]
    assert value not in cleaned and "[REDACTED]" in cleaned
    assert "Deploy notes." in cleaned and "End." in cleaned


def test_sanitizer_leaves_ordinary_text_alone(tmp_path):
    ao = Ompa(vault_path=tmp_path, enable_semantic=False)
    text = "The keynote covered token economics; ask-me-anything is on Friday. See sk-1 ticket."
    assert ao._sanitize_content(text) == text


def test_export_redacts_frontmatter(tmp_path):
    shared, personal = tmp_path / "shared", tmp_path / "personal"
    ao = Ompa(shared_vault_path=shared, personal_vault_path=personal, enable_semantic=False)
    note = personal / "brain" / "keys.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(
        "---\ndate: 2026-04-11\napi_key: abc123def456\nservice:\n  client_secret: s3cr3t-value\n"
        "owner: dana\n---\nRotation steps.\n",
        encoding="utf-8",
    )
    assert ao.export_to_shared("brain/keys.md", confirm=False)["success"]
    exported = (shared / "brain" / "keys.md").read_text(encoding="utf-8")
    assert "abc123def456" not in exported and "s3cr3t-value" not in exported
    assert "owner: dana" in exported and "Rotation steps." in exported


# ---------------------------------------------------------------------------
# rsync never replaces a newer file
# ---------------------------------------------------------------------------


def test_rsync_does_not_overwrite_newer_files_by_default(monkeypatch):
    from ompa.sync import RsyncBackend

    monkeypatch.setattr("ompa.sync.rsync.shutil.which", lambda _name: "/usr/bin/rsync")
    assert "--update" in RsyncBackend(remote="host:/vault")._build_rsync_cmd("a/", "b/")
    assert "--update" not in RsyncBackend(remote="host:/vault", update=False)._build_rsync_cmd(
        "a/", "b/"
    )
