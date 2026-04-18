"""Cross-platform path behavior.

These tests lock in the invariants that keep OMPA working on Windows
(backslash paths, mixed separators in user input) without breaking
POSIX. Run on both CI runners.
"""

import sys
import tempfile
from pathlib import Path


def test_vault_rglob_works_under_nested_paths():
    """rglob + str(path) exclusion must work regardless of separator."""
    from ompa.vault import Vault

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "brain").mkdir()
        (root / "work" / "active").mkdir(parents=True)
        (root / ".git").mkdir()

        (root / "brain" / "note.md").write_text("# Brain", encoding="utf-8")
        (root / "work" / "active" / "proj.md").write_text("# Proj", encoding="utf-8")
        # Exclude pattern `.git` must drop this even when path has
        # OS-native separators.
        (root / ".git" / "HEAD.md").write_text("# Secret", encoding="utf-8")

        v = Vault(root)
        notes = v.list_notes()
        paths = [str(n.path) for n in notes]
        assert any("brain" in p for p in paths)
        assert any("proj" in p for p in paths)
        assert not any(".git" in p for p in paths), "Exclude pattern leaked .git note"


def test_write_to_forward_slash_file_path_normalizes():
    """Ompa.write(file_path='work/active/x.md') must work on Windows too."""
    from ompa import Ompa

    with tempfile.TemporaryDirectory() as tmp:
        ao = Ompa(vault_path=tmp, enable_semantic=False)
        # User input uses forward slashes — should resolve to OS-native path
        ao.write(
            content="Hello",
            file_path="work/active/hello.md",
            tags=["test"],
        )
        assert (Path(tmp) / "work" / "active" / "hello.md").exists()


def test_safe_resolve_rejects_mixed_separator_escape():
    """Mixed `/` and `\\` in user input must not defeat traversal checks."""
    from ompa.vault import _safe_resolve

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        # Forward-slash escape
        try:
            _safe_resolve(base, "../../etc/passwd")
        except ValueError:
            pass
        else:
            raise AssertionError("forward-slash traversal should be blocked")

        if sys.platform == "win32":
            # Backslash escape on Windows
            try:
                _safe_resolve(base, "..\\..\\Windows\\System32")
            except ValueError:
                pass
            else:
                raise AssertionError("backslash traversal should be blocked")


def test_palace_drawer_paths_are_posix_style():
    """Palace stores relative drawer paths — must round-trip across platforms
    by using forward slashes consistently (the Obsidian wikilink convention)."""
    from ompa.palace import Palace

    with tempfile.TemporaryDirectory() as tmp:
        p = Palace(Path(tmp))
        p.create_wing("work", type="projects")
        p.create_room("work", "auth")
        # Whatever separator caller gives, it should survive a reload
        p.link_drawer("work", "auth", "work/active/auth.md")

        p2 = Palace(Path(tmp))
        drawers = p2.get_drawers("work", "auth")
        assert any("auth.md" in d for d in drawers)
