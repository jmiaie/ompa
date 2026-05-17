"""Tests for ompa.vault — Vault management."""

import tempfile

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

    def test_safe_resolve_prefix_collision_blocked(self):
        from pathlib import Path

        from ompa.vault import _safe_resolve

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            base = root / "vault"
            sibling = root / "vault-evil"
            base.mkdir()
            sibling.mkdir()

            with pytest.raises(ValueError, match="Path traversal blocked"):
                _safe_resolve(base, "../vault-evil/pwn.md")

    def test_note_save_utf8(self):
        from pathlib import Path

        from ompa.vault import Note

        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "unicode.md"
            note = Note(path=path, content="Héllo wörld 日本語")
            note.save()
            loaded = Note.from_file(path)
            assert "日本語" in loaded.content

    def test_safe_resolve_prefix_collision(self):
        """_safe_resolve should block prefix-collision bypasses like vault vs vault-evil."""
        from pathlib import Path

        from ompa.vault import _safe_resolve

        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "vault"
            base.mkdir()
            evil = Path(tmpdir) / "vault-evil"
            evil.mkdir()
            with pytest.raises(ValueError, match="Path traversal blocked"):
                _safe_resolve(base, "../vault-evil/secret.md")

    def test_search_by_name(self):
        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
            vault.update_brain_note("Auth Design", "Authentication design doc")
            results = vault.search_by_name("auth")
            assert len(results) >= 1


class TestOrphanAndBrainFixes:
    """Test orphan detection and brain note counting fixes."""

    def test_orphans_resolve_wikilinks_by_filename(self):
        """Wikilinks should resolve by filename even in subdirectories."""
        from pathlib import Path

        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
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
            assert "Auth" not in orphan_names
            assert "Design" not in orphan_names

    def test_orphans_with_md_extension_in_wikilink(self):
        """Wikilinks like [[SOUL.md]] should resolve correctly."""
        from pathlib import Path

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
            assert "SOUL" not in orphan_names

    def test_brain_notes_count_by_frontmatter_wing(self):
        """Notes with wing=brain in frontmatter should count as brain notes."""
        from pathlib import Path

        from ompa import Vault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Vault(tmpdir)
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
            assert stats["brain_notes"] >= 2

    def test_brain_notes_count_in_brain_folder(self):
        """Notes in brain/ folder should always count."""
        from pathlib import Path

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
        from pathlib import Path

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
            orphan_names = [o.path.stem for o in orphans]
            assert "Lonely" in orphan_names
            assert "A" not in orphan_names
