"""Tests for ompa dual-vault architecture."""

import tempfile

import pytest


class TestDualVault:
    """Test dual-vault architecture."""

    def test_single_vault_backward_compat(self):
        """Single-vault init should still work identically."""
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            assert ao.is_dual_vault is False
            assert ao.personal_vault is None
            result = ao.session_start()
            assert result.success

    def test_dual_vault_init(self):
        """Dual-vault should create both vault structures."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            assert ao.is_dual_vault is True
            assert ao.vault is not None
            assert ao.personal_vault is not None
            assert (shared / "brain").is_dir()
            assert (personal / "brain").is_dir()

    def test_auto_classify_shared(self):
        """Team decisions should route to shared vault."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig()
        target = config.classify_content(
            "We decided to use Postgres for the database",
            tags=["@team", "decision"],
        )
        assert target == VaultTarget.SHARED

    def test_auto_classify_personal(self):
        """Content with credentials should route to personal vault."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig()
        target = config.classify_content(
            "My API key is sk-abc123xyz",
            tags=["api-keys"],
        )
        assert target == VaultTarget.PERSONAL

    def test_auto_classify_personal_tag(self):
        """@private tag should route to personal."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig()
        target = config.classify_content(
            "Some random note",
            tags=["@private"],
        )
        assert target == VaultTarget.PERSONAL

    def test_auto_classify_folder_rules(self):
        """Folder-based routing should work."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig()
        assert (
            config.classify_content("Note", file_path="brain/North Star.md")
            == VaultTarget.SHARED
        )
        assert (
            config.classify_content("Note", file_path="personal/config.md")
            == VaultTarget.PERSONAL
        )

    def test_write_to_shared(self):
        """write() with vault='shared' should write to shared vault."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            result = ao.write(
                "We agreed to use PostgreSQL",
                vault="shared",
                tags=["decision"],
            )
            assert result["vault"] == "shared"
            assert Path(result["path"]).exists()

    def test_write_to_personal(self):
        """write() with vault='personal' should write to personal vault."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            result = ao.write(
                "My secret API key: sk-12345",
                vault="personal",
                tags=["api-keys"],
            )
            assert result["vault"] == "personal"
            assert Path(result["path"]).exists()

    def test_write_auto_classify(self):
        """write() without vault= should auto-classify."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                isolation_mode="permissive",
                enable_semantic=False,
            )
            result = ao.write("password: hunter2")
            assert result["vault"] == "personal"

    def test_write_path_traversal_blocked(self):
        """write() should reject paths that escape the target vault."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            with pytest.raises(ValueError, match="Path traversal blocked"):
                ao.write("escaped", vault="shared", file_path="../escape.md")

    def test_export_to_shared(self):
        """export_to_shared should copy note to shared vault."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            note_dir = personal / "brain"
            note_dir.mkdir(parents=True, exist_ok=True)
            (note_dir / "idea.md").write_text(
                "---\ndate: 2026-04-11\n---\nRefactor the auth layer.",
                encoding="utf-8",
            )
            result = ao.export_to_shared("brain/idea.md", confirm=False)
            assert result["success"] is True
            assert (shared / "brain" / "idea.md").exists()

    def test_export_to_shared_path_traversal_blocked(self):
        """export_to_shared() should reject note paths outside personal vault."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            result = ao.export_to_shared("../../outside.md", confirm=False)
            assert result["success"] is False
            assert "Invalid note_path" in result["error"]

    def test_export_sanitizes_content(self):
        """export_to_shared should redact credentials."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            note_dir = personal / "brain"
            note_dir.mkdir(parents=True, exist_ok=True)
            (note_dir / "keys.md").write_text(
                "---\ndate: 2026-04-11\n---\nAPI key: sk-abcdefghijklmnopqrstuvwxyz",
                encoding="utf-8",
            )
            result = ao.export_to_shared("brain/keys.md", confirm=False, sanitize=True)
            assert result["success"] is True
            exported = (shared / "brain" / "keys.md").read_text(encoding="utf-8")
            assert "sk-abcdefghijklmnopqrstuvwxyz" not in exported
            assert "[REDACTED]" in exported

    def test_import_to_personal(self):
        """import_to_personal should copy note to personal vault."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            note_dir = shared / "brain"
            note_dir.mkdir(parents=True, exist_ok=True)
            (note_dir / "spec.md").write_text(
                "---\ndate: 2026-04-11\n---\nAPI spec.",
                encoding="utf-8",
            )
            result = ao.import_to_personal("brain/spec.md", link_back=True)
            assert result["success"] is True
            imported = (personal / "brain" / "spec.md").read_text(encoding="utf-8")
            assert "Imported from shared" in imported

    def test_import_to_personal_path_traversal_blocked(self):
        """import_to_personal() should reject note paths outside shared vault."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            result = ao.import_to_personal("../../outside.md", link_back=True)
            assert result["success"] is False
            assert "Invalid note_path" in result["error"]

    def test_isolation_strict_export_preview(self):
        """In strict mode, export with confirm=True should return preview."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                isolation_mode="strict",
                enable_semantic=False,
            )
            note_dir = personal / "brain"
            note_dir.mkdir(parents=True, exist_ok=True)
            (note_dir / "draft.md").write_text(
                "---\ndate: 2026-04-11\n---\nDraft idea.",
                encoding="utf-8",
            )
            result = ao.export_to_shared("brain/draft.md", confirm=True)
            assert result["action"] == "preview"
            assert "Draft idea" in result["preview"]

    def test_dual_vault_sync(self):
        """sync() should sync both vaults in dual mode."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            (shared / "brain").mkdir(parents=True, exist_ok=True)
            (shared / "brain" / "S.md").write_text(
                "---\ndate: 2026-04-11\n---\n[[Link]].",
                encoding="utf-8",
            )
            (personal / "brain").mkdir(parents=True, exist_ok=True)
            (personal / "brain" / "P.md").write_text(
                "---\ndate: 2026-04-11\n---\n[[PLink]].",
                encoding="utf-8",
            )
            result = ao.sync()
            assert result["kg_triples"] > 0
            assert "personal_kg_triples" in result
            assert result["personal_kg_triples"] > 0

    def test_cross_vault_search(self):
        """search() with vaults=['shared','personal'] should search both."""
        from pathlib import Path

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            (shared / "brain").mkdir(parents=True, exist_ok=True)
            (shared / "brain" / "Auth-Team.md").write_text(
                "---\ndate: 2026-04-11\n---\nTeam auth decision.",
                encoding="utf-8",
            )
            (personal / "brain").mkdir(parents=True, exist_ok=True)
            (personal / "brain" / "Auth-Private.md").write_text(
                "---\ndate: 2026-04-11\n---\nMy private auth notes.",
                encoding="utf-8",
            )
            results = ao.search("Auth", vaults=["shared", "personal"])
            paths = [r.path for r in results]
            assert any("Auth-Team" in p for p in paths)
            assert any("Auth-Private" in p for p in paths)

    def test_write_path_traversal_blocked_single_vault(self):
        """write() should reject file paths that escape the vault."""
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            with pytest.raises(ValueError, match="Path traversal blocked"):
                ao.write("evil content", file_path="../../etc/passwd")
