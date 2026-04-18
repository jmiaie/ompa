"""Tests for dual_vault."""

import tempfile
from pathlib import Path

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
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            from pathlib import Path

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

    # ------------------------------------------------------------------
    # P2.5 — indicator regex must use word boundaries, not substring match
    # ------------------------------------------------------------------

    def test_indicator_no_false_positive_sk_prefix(self):
        """`sk-` indicator must NOT fire on words that merely contain `sk-`
        as a substring (e.g. "desk-work")."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.SHARED)
        target = config.classify_content(
            "Bought a standing desk-work setup over the weekend.",
        )
        # No personal indicators, no shared indicators, no folder — falls to
        # default (shared), proving `sk-` did NOT match `desk-work`.
        assert target == VaultTarget.SHARED

    def test_indicator_sk_prefix_still_fires_on_real_key(self):
        """`sk-` must still catch an actual OpenAI-style secret."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.SHARED)
        target = config.classify_content(
            "Config: OPENAI_API_KEY=sk-AbCdEf1234567890xyz",
        )
        assert target == VaultTarget.PERSONAL

    def test_indicator_no_false_positive_token_in_tokenize(self):
        """`token` indicator must NOT fire on `tokenize` / `tokenized`."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.SHARED)
        target = config.classify_content(
            "We tokenize the input using a BPE scheme; the tokenizer is fast.",
        )
        assert target == VaultTarget.SHARED

    def test_indicator_token_still_fires_whole_word(self):
        """`token` must still match when the word stands alone."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.SHARED)
        target = config.classify_content(
            "I stored the auth token in a local file.",
        )
        assert target == VaultTarget.PERSONAL

    def test_indicator_akia_case_sensitive_prefix(self):
        """`AKIA` indicator is uppercase-only; lowercase `akia` substring
        (as in a random word) must NOT fire."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.SHARED)
        # `akia` appears lowercased and inside another word — must not match.
        assert (
            config.classify_content("The blackiawave was long and slow.")
            == VaultTarget.SHARED
        )
        # Real AWS-style key — must fire.
        assert (
            config.classify_content("AWS key rotated: AKIAIOSFODNN7EXAMPLE today.")
            == VaultTarget.PERSONAL
        )

    def test_indicator_decision_no_false_positive_on_decisions(self):
        """`decision` indicator uses \\b boundaries so it does not match
        `decisions` or `decisioned` — users who want plural/variant forms
        should add them to the indicator list explicitly."""
        from ompa.config import DualVaultConfig, VaultTarget

        config = DualVaultConfig(default_vault=VaultTarget.PERSONAL)
        # "decisions" (plural) — no match, falls through to default.
        target = config.classify_content(
            "Made several decisions over lunch.",
        )
        assert target == VaultTarget.PERSONAL

    def test_write_to_shared(self):
        """write() with vault='shared' should write to shared vault."""

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
            # Content with API key should go to personal
            result = ao.write("password: hunter2")
            assert result["vault"] == "personal"

    def test_export_to_shared(self):
        """export_to_shared should copy note to shared vault."""

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            # Create a personal note
            note_dir = personal / "brain"
            note_dir.mkdir(parents=True, exist_ok=True)
            (note_dir / "idea.md").write_text(
                "---\ndate: 2026-04-11\n---\nRefactor the auth layer.",
                encoding="utf-8",
            )
            # Export (with confirm=False to actually export)
            result = ao.export_to_shared("brain/idea.md", confirm=False)
            assert result["success"] is True
            assert (shared / "brain" / "idea.md").exists()

    def test_export_sanitizes_content(self):
        """export_to_shared should redact credentials."""

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

    def test_sanitize_content_covers_broad_secret_set(self):
        """P0.9: _sanitize_content must catch common cloud/service credentials."""

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(
                shared_vault_path=Path(tmpdir) / "s",
                personal_vault_path=Path(tmpdir) / "p",
                enable_semantic=False,
            )
            # Fixtures intentionally built from prefix + low-entropy "A" bodies
            # so they exercise the regexes without tripping git-hosting secret
            # scanners. Prefix strings are split into two adjacent literals so
            # a naive regex scan of this file won't hit a canonical-format
            # secret either.
            secrets = {
                "openai": ("sk-" "proj-") + "A" * 36,
                "anthropic": ("sk-" "ant-") + "A" * 36,
                "aws_akia": ("AK" "IA") + "A" * 16,
                "aws_asia": ("AS" "IA") + "A" * 16,
                "github_classic": ("gh" "p_") + "A" * 36,
                "github_oauth": ("gh" "o_") + "A" * 36,
                "github_fg": ("github_" "pat_") + "A" * 22,
                "gitlab": ("gl" "pat-") + "A" * 20,
                "slack_bot": ("xox" "b-") + "A" * 20,
                "google": ("AI" "za") + "A" * 35,
                "stripe_live": ("sk_" "live_") + "A" * 20,
                "stripe_restricted": ("rk_" "live_") + "A" * 20,
                "jwt": (
                    ("ey" "J") + "A" * 15 + "." + ("ey" "J") + "A" * 15 + "." + "A" * 20
                ),
                "azure": "AccountKey=" + "A" * 60,
                "mongodb": "mongodb+srv://user:pw@cluster.example.net/db",
                "postgres": "postgresql://user:pw@db.example.com:5432/app",
                "pem": (
                    "-----BEGIN RSA PRIVATE KEY-----\n"
                    + "A" * 20
                    + "\n-----END RSA PRIVATE KEY-----"
                ),
                "generic_password": "password: hunter2",
                "generic_bearer": "Bearer=abc123xyz",
            }

            for label, secret in secrets.items():
                sanitized = ao._sanitize_content(f"note with {secret} inside")
                if label in ("generic_password", "generic_bearer"):
                    # generic KV redaction preserves the key, redacts value
                    assert "[REDACTED]" in sanitized, label
                else:
                    assert "[REDACTED]" in sanitized, label
                    # The literal secret body should not survive.
                    # For JWT, the full token string is redacted.
                    assert secret not in sanitized, label

    def test_sanitize_content_preserves_safe_text(self):
        """Guard against over-redaction of ordinary words."""

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(
                shared_vault_path=Path(tmpdir) / "s",
                personal_vault_path=Path(tmpdir) / "p",
                enable_semantic=False,
            )
            text = (
                "The desk-work document mentions a token review. "
                "We stored secrets in a vault. Not a real password."
            )
            sanitized = ao._sanitize_content(text)
            # Plain prose about "secret" and "token" without a key=value
            # pattern should pass through unchanged.
            assert "desk-work" in sanitized
            assert "token review" in sanitized

    def test_import_to_personal(self):
        """import_to_personal should copy note to personal vault."""

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            # Create a shared note
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

    def test_cross_vault_search(self):
        """search() with vaults=['shared','personal'] should search both."""

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            # Create notes in both vaults with searchable names
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
            # Search both (by filename match since semantic is off)
            results = ao.search("Auth", vaults=["shared", "personal"])
            paths = [r.path for r in results]
            assert any("Auth-Team" in p for p in paths)
            assert any("Auth-Private" in p for p in paths)

    def test_isolation_strict_export_preview(self):
        """In strict mode, export with confirm=True should return preview."""

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

    def test_mcp_write_tool(self):
        """MCP ao_write tool should work."""
        from ompa.mcp_server import handle_call_tool

        with tempfile.TemporaryDirectory() as tmpdir:
            result = handle_call_tool(
                "ao_write",
                {"content": "Test note content", "vault_path": tmpdir},
            )
            assert "vault" in result
            assert "path" in result

    def test_classifier_vault_target(self):
        """Classifier should suggest vault targets."""
        from ompa import MessageClassifier

        c = MessageClassifier()
        assert c.classify_vault_target("We decided to use Postgres") == "shared"
        assert c.classify_vault_target("random stuff") == "ambiguous"

    def test_export_to_shared_path_traversal_blocked(self):
        """export_to_shared() should reject note paths outside personal vault."""

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

    def test_import_to_personal_path_traversal_blocked(self):
        """import_to_personal() should reject note paths outside shared vault."""

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            result = ao.import_to_personal("../../outside.md")
            assert result["success"] is False
            assert "Invalid note_path" in result["error"]

    def test_write_path_traversal_blocked(self):
        """write() should reject file paths that escape the vault."""
        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            ao = Ompa(tmpdir, enable_semantic=False)
            with pytest.raises(ValueError, match="Path traversal blocked"):
                ao.write("evil content", file_path="../../etc/passwd")

    def test_dual_vault_sync(self):
        """sync() should sync both vaults in dual mode."""

        from ompa import Ompa

        with tempfile.TemporaryDirectory() as tmpdir:
            shared = Path(tmpdir) / "shared"
            personal = Path(tmpdir) / "personal"
            ao = Ompa(
                shared_vault_path=shared,
                personal_vault_path=personal,
                enable_semantic=False,
            )
            # Create notes in both
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


