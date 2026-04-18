"""Tests for vault."""

import os
import tempfile
from pathlib import Path

import pytest


class TestVault:
    """Test vault management."""

    def test_init_creates_structure(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            assert (vault.vault_path / "brain").is_dir()
            assert (vault.vault_path / "work" / "active").is_dir()
            assert (vault.vault_path / "org" / "people").is_dir()

    def test_get_stats_empty_vault(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            stats = vault.get_stats()
            assert stats["total_notes"] == 0
            assert stats["orphans"] == 0

    def test_update_and_get_brain_note(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("Test Note", "Hello world")
            note = vault.get_brain_note("Test Note")
            assert note is not None
            assert "Hello world" in note.content

    def test_brain_note_path_traversal_blocked(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            with pytest.raises(ValueError, match="Invalid brain note name"):
                vault.get_brain_note("../../etc/passwd")

    def test_update_brain_note_path_traversal_blocked(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            with pytest.raises(ValueError, match="Invalid brain note name"):
                vault.update_brain_note("../../etc/evil", "pwned")

    def test_validate_write_blocks_outside_vault(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            result = vault.validate_write("../../etc/passwd")
            assert result["valid"] is False
            assert "outside the vault" in result["warnings"][0]

    def test_create_from_template_path_traversal_blocked(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            with pytest.raises((ValueError, FileNotFoundError)):
                vault.create_from_template("../../etc/evil", "../../tmp/pwned.md")

    def test_note_save_utf8(self):

        from ompa.vault import Note

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unicode.md"
            note = Note(path=path, content="Héllo wörld 日本語")
            note.save()
            loaded = Note.from_file(path)
            assert "日本語" in loaded.content

    def test_safe_resolve_prefix_collision(self):
        """_safe_resolve should block prefix-collision bypasses like vault vs vault-evil."""

        from ompa.vault import _safe_resolve

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "vault"
            base.mkdir()
            evil = Path(tmpdir) / "vault-evil"
            evil.mkdir()
            # This should NOT be allowed — "vault-evil" starts with "vault" as a string
            # but is NOT a child of "vault"
            with pytest.raises(ValueError, match="Path traversal blocked"):
                _safe_resolve(base, "../vault-evil/secret.md")

    def test_search_by_name(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("Auth Design", "Authentication design doc")
            results = vault.search_by_name("auth")
            assert len(results) >= 1

    def test_list_notes_cache_reuses_parsed_note(self):
        """Unchanged files should be served from the cache as the same Note
        object across calls — this is the core P1.1 optimization."""
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("cache-test", "hello world")
            first = {n.path: n for n in vault.list_notes()}
            second = {n.path: n for n in vault.list_notes()}
            # Every path present in both scans must return the identical
            # object — proving the cache returned the cached Note instead
            # of re-parsing from disk.
            shared = set(first) & set(second)
            assert shared, "Expected at least one note to appear in both scans"
            for path in shared:
                assert first[path] is second[path]

    def test_list_notes_cache_invalidates_on_mtime_change(self):
        """When a file's mtime changes, the cache must re-parse it."""
        import time

        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("mtime-test", "original content")
            target = vault.config.brain_folder / "mtime-test.md"
            [initial] = [n for n in vault.list_notes() if n.path == target]
            assert "original content" in initial.content

            # Rewrite with a distinctly later mtime. Use os.utime to avoid
            # depending on real time passing during the test.
            target.write_text(
                "---\ntitle: mtime-test\n---\nupdated content",
                encoding="utf-8",
            )
            new_time = time.time() + 10
            os.utime(target, (new_time, new_time))

            [refreshed] = [n for n in vault.list_notes() if n.path == target]
            assert "updated content" in refreshed.content
            assert refreshed is not initial

    def test_list_notes_cache_explicit_invalidation(self):
        """invalidate_path / invalidate_cache drop cached entries."""
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("invalidate-test", "content")
            target = vault.config.brain_folder / "invalidate-test.md"

            _ = vault.list_notes()
            assert target in vault._note_cache

            vault.invalidate_path(target)
            assert target not in vault._note_cache

            _ = vault.list_notes()
            assert target in vault._note_cache

            vault.invalidate_cache()
            assert vault._note_cache == {}

    def test_extract_wikilinks_shared_behavior(self):
        """P2.4: module-level extract_wikilinks normalizes consistently.

        Both Note._extract_wikilinks and KG populate use this function,
        so this test documents the canonical contract:
          - [[target|display]] → keep target, drop display
          - [[SOUL.md]]        → strip trailing .md
          - whitespace trimmed, empty targets dropped
        """
        from ompa.vault import extract_wikilinks

        result = extract_wikilinks(
            "See [[SOUL.md]] and [[Design|the design doc]] and [[ ]] and [[Auth]]"
        )
        assert result == ["SOUL", "Design", "Auth"]

    def test_extract_wikilinks_empty_and_edge_cases(self):
        """Edge cases for the shared wikilink extractor."""
        from ompa.vault import extract_wikilinks

        assert extract_wikilinks("") == []
        assert extract_wikilinks("no links here") == []
        assert extract_wikilinks("[[]]") == []
        assert extract_wikilinks("[[ .md ]]") == []
        # Nested-looking patterns: regex is non-greedy so inner wins
        assert extract_wikilinks("[[a]] [[b]]") == ["a", "b"]



class TestOrphanAndBrainFixes:
    """Test orphan detection and brain note counting fixes."""

    def test_orphans_resolve_wikilinks_by_filename(self):
        """Wikilinks should resolve by filename even in subdirectories."""

        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            # Create notes in subdirectories
            skills = Path(tmpdir) / "work" / "active"
            skills.mkdir(parents=True, exist_ok=True)
            (skills / "Auth.md").write_text(
                "---\ndate: 2026-04-10\n---\nSee [[Design]].",
                encoding="utf-8",
            )
            (skills / "Design.md").write_text(
                "---\ndate: 2026-04-10\n---\nDesign doc for [[Auth]].",
                encoding="utf-8",
            )
            orphans = vault.find_orphans()
            orphan_names = [o.path.stem for o in orphans]
            # Auth and Design link to each other — neither should be orphaned
            assert "Auth" not in orphan_names
            assert "Design" not in orphan_names

    def test_orphans_with_md_extension_in_wikilink(self):
        """Wikilinks like [[SOUL.md]] should resolve correctly."""

        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            brain = Path(tmpdir) / "brain"
            brain.mkdir(parents=True, exist_ok=True)
            (brain / "SOUL.md").write_text(
                "---\ndate: 2026-04-10\n---\nSoul content.",
                encoding="utf-8",
            )
            (brain / "Index.md").write_text(
                "---\ndate: 2026-04-10\n---\nLinks: [[SOUL.md]]",
                encoding="utf-8",
            )
            orphans = vault.find_orphans()
            orphan_names = [o.path.stem for o in orphans]
            # SOUL is linked from Index, so not an orphan
            assert "SOUL" not in orphan_names

    def test_brain_notes_count_by_frontmatter_wing(self):
        """Notes with wing=brain in frontmatter should count as brain notes."""

        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            # Put notes outside brain/ folder but with wing: brain
            root = Path(tmpdir)
            (root / "SOUL.md").write_text(
                "---\ndate: 2026-04-10\nwing: brain\n---\nSoul content.",
                encoding="utf-8",
            )
            (root / "IDENTITY.md").write_text(
                "---\ndate: 2026-04-10\nwing: brain\n---\nIdentity.",
                encoding="utf-8",
            )
            (root / "SKILLS.md").write_text(
                "---\ndate: 2026-04-10\nwing: work\n---\nSkills.",
                encoding="utf-8",
            )
            stats = vault.get_stats()
            # Should count the 2 wing=brain notes
            assert stats["brain_notes"] >= 2

    def test_brain_notes_count_in_brain_folder(self):
        """Notes in brain/ folder should always count."""

        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            brain = Path(tmpdir) / "brain"
            brain.mkdir(parents=True, exist_ok=True)
            (brain / "North Star.md").write_text(
                "---\ndate: 2026-04-10\n---\nGoals.",
                encoding="utf-8",
            )
            (brain / "Decisions.md").write_text(
                "---\ndate: 2026-04-10\n---\nKey decisions.",
                encoding="utf-8",
            )
            stats = vault.get_stats()
            assert stats["brain_notes"] >= 2

    def test_orphan_stats_match_find_orphans(self):
        """get_stats() orphan count should match find_orphans() length."""

        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            brain = Path(tmpdir) / "brain"
            brain.mkdir(parents=True, exist_ok=True)
            (brain / "A.md").write_text(
                "---\ndate: 2026-04-10\n---\nSee [[B]].",
                encoding="utf-8",
            )
            (brain / "B.md").write_text(
                "---\ndate: 2026-04-10\n---\nSee [[A]].",
                encoding="utf-8",
            )
            (brain / "Lonely.md").write_text(
                "---\ndate: 2026-04-10\n---\nNo links here.",
                encoding="utf-8",
            )
            stats = vault.get_stats()
            orphans = vault.find_orphans()
            assert stats["orphans"] == len(orphans)
            # A and B link to each other, Lonely has no incoming links
            orphan_names = [o.path.stem for o in orphans]
            assert "Lonely" in orphan_names
            assert "A" not in orphan_names


