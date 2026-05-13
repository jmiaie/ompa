"""
Dual-vault operations for OMPA.

This module provides the DualVaultMixin, which adds cross-vault transfer
methods to the Ompa class: write, export_to_shared, import_to_personal,
and migrate_to_dual_vault.

Keeping these here separates dual-vault concerns from the core lifecycle
logic in core.py, without introducing circular imports (the mixin uses
TYPE_CHECKING to reference Ompa only for type hints).
"""

import logging
import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .vault import Note, _safe_resolve
from .config import IsolationMode, VaultTarget

if TYPE_CHECKING:
    from .vault import Vault
    from .palace import Palace
    from .knowledge_graph import KnowledgeGraph
    from .classifier import MessageClassifier
    from .config import DualVaultConfig

logger = logging.getLogger(__name__)


def _sanitize_content(content: str) -> str:
    """Remove sensitive markers and credentials from content."""
    content = re.sub(r"@private\b", "", content)
    content = re.sub(r"#personal\b", "", content)
    content = re.sub(r"(sk-[a-zA-Z0-9]{20,})", "[REDACTED]", content)
    content = re.sub(r"(AKIA[A-Z0-9]{16})", "[REDACTED]", content)
    content = re.sub(
        r"(token|password|secret|api_key|api-key)\s*[:=]\s*\S+",
        r"\1: [REDACTED]",
        content,
        flags=re.IGNORECASE,
    )
    return content


class DualVaultMixin:
    """
    Mixin that adds dual-vault transfer operations to Ompa.

    Expects the host class to provide the attributes set by Ompa.__init__:
    vault, vault_path, personal_vault, kg, personal_kg, palace,
    dual_config, is_dual_vault, classifier,
    _auto_update_kg (method).
    """

    # Declare expected attributes so mypy knows the shape of the host class.
    # These are provided by Ompa.__init__ — the mixin never instantiates them.
    if TYPE_CHECKING:
        vault: "Vault"
        vault_path: Path
        personal_vault: Optional["Vault"]
        personal_kg: Optional["KnowledgeGraph"]
        kg: "KnowledgeGraph"
        palace: "Palace"
        dual_config: "DualVaultConfig"
        classifier: "MessageClassifier"
        is_dual_vault: bool

    def write(
        self,
        content: str,
        file_path: Optional[str] = None,
        tags: Optional[list] = None,
        vault: Optional[str] = None,
    ) -> dict:
        """
        Write content to the appropriate vault.

        In dual-vault mode, auto-classifies content unless vault is specified.
        In single-vault mode, writes to the single vault.

        Args:
            content: Note content to write
            file_path: Target file path (relative to vault root)
            tags: Tags for classification and frontmatter
            vault: Force target vault: "shared" or "personal"

        Returns:
            dict with {vault, path, classified_as}
        """
        tags = tags or []

        # Determine target vault
        if not self.is_dual_vault:
            target = VaultTarget.SHARED
            target_vault = self.vault
        elif vault:
            target = VaultTarget(vault)
            target_vault = (
                self.vault if target == VaultTarget.SHARED else self.personal_vault
            )
        elif self.dual_config.isolation_mode == IsolationMode.MANUAL:
            target = self.dual_config.default_vault
            target_vault = (
                self.vault if target == VaultTarget.SHARED else self.personal_vault
            )
        else:
            # Auto-classify
            target = self.dual_config.classify_content(
                content, tags=tags, file_path=file_path
            )
            target_vault = (
                self.vault if target == VaultTarget.SHARED else self.personal_vault
            )

        # Build file path if not provided
        if not file_path:
            classification = self.classifier.classify(content[:200])
            folder = classification.suggested_folder
            words = re.sub(r"[^\w\s]", "", content[:40]).split()
            name = "-".join(words[:5]) if words else "note"
            file_path = f"{folder}{name}.md"

        # Write the note
        from datetime import datetime

        frontmatter: dict[str, object] = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "tags": tags,
            "vault": target.value,
        }

        full_path = _safe_resolve(target_vault.vault_path, file_path)
        note = Note(path=full_path, frontmatter=frontmatter, content=content)
        note.save()

        # Update KG + index
        target_kg = self.kg if target == VaultTarget.SHARED else self.personal_kg
        if target_kg:
            target_kg.populate_from_note(full_path, target_vault.vault_path)

        return {
            "vault": target.value,
            "path": str(full_path),
            "classified_as": target.value,
        }

    def export_to_shared(
        self,
        note_path: str,
        confirm: bool = True,
        sanitize: bool = True,
    ) -> dict:
        """
        Export a note from personal vault to shared vault.

        Args:
            note_path: Path relative to personal vault root
            confirm: If True, returns preview without writing (for confirmation)
            sanitize: Strip personal markers (@private, credentials, etc.)

        Returns:
            dict with {success, source, target, sanitized, preview}
        """
        if not self.is_dual_vault:
            return {"success": False, "error": "Not in dual-vault mode"}

        try:
            source = _safe_resolve(self.dual_config.personal_path, note_path)
            target = _safe_resolve(self.dual_config.shared_path, note_path)
        except ValueError:
            return {"success": False, "error": f"Invalid note_path: {note_path}"}

        if self.dual_config.isolation_mode == IsolationMode.STRICT and confirm:
            if not source.exists():
                return {"success": False, "error": f"Note not found: {note_path}"}

            note = Note.from_file(source)
            content = _sanitize_content(note.content) if sanitize else note.content

            return {
                "success": True,
                "action": "preview",
                "source": str(source),
                "target": str(target),
                "sanitized": sanitize,
                "preview": content[:500],
            }

        if not source.exists():
            return {"success": False, "error": f"Note not found: {note_path}"}

        note = Note.from_file(source)
        if sanitize:
            note.content = _sanitize_content(note.content)

        note.frontmatter["vault"] = "shared"
        note.frontmatter.pop("@private", None)
        note.path = target
        note.save()

        self.kg.populate_from_note(target, self.dual_config.shared_path)

        logger.info("Exported %s to shared vault", note_path)
        return {
            "success": True,
            "action": "exported",
            "source": str(source),
            "target": str(target),
        }

    def import_to_personal(
        self,
        note_path: str,
        link_back: bool = True,
    ) -> dict:
        """
        Import a note from shared vault to personal vault.

        Args:
            note_path: Path relative to shared vault root
            link_back: Maintain a wikilink reference to the shared original

        Returns:
            dict with {success, source, target}
        """
        if not self.is_dual_vault:
            return {"success": False, "error": "Not in dual-vault mode"}

        try:
            source = _safe_resolve(self.dual_config.shared_path, note_path)
            target = _safe_resolve(self.dual_config.personal_path, note_path)
        except ValueError:
            return {"success": False, "error": f"Invalid note_path: {note_path}"}

        if not source.exists():
            return {"success": False, "error": f"Note not found: {note_path}"}

        note = Note.from_file(source)
        note.frontmatter["vault"] = "personal"
        note.frontmatter["imported_from"] = str(source)

        if link_back:
            note.content += f"\n\n---\n*Imported from shared: [[{note_path}]]*"

        note.path = target
        note.save()

        if self.personal_kg:
            self.personal_kg.populate_from_note(target, self.dual_config.personal_path)

        logger.info("Imported %s to personal vault", note_path)
        return {
            "success": True,
            "source": str(source),
            "target": str(target),
        }

    def migrate_to_dual_vault(
        self,
        shared_path,
        personal_path,
        classification_rules: str = "auto",
    ) -> dict:
        """
        Migrate a single-vault OMPA to dual-vault architecture.

        Args:
            shared_path: Path for the shared vault
            personal_path: Path for the personal vault
            classification_rules: "auto" to auto-classify, "all-shared" to keep all in shared

        Returns:
            dict with migration stats
        """
        shared_path = Path(shared_path).expanduser()
        personal_path = Path(personal_path).expanduser()

        shared_path.mkdir(parents=True, exist_ok=True)
        personal_path.mkdir(parents=True, exist_ok=True)

        shared_count = 0
        personal_count = 0

        notes = self.vault.list_notes()
        for note in notes:
            if classification_rules == "auto":
                target = self.dual_config.classify_content(
                    note.content,
                    tags=[str(t) for t in (note.frontmatter.get("tags") or [])],
                    file_path=str(note.path),
                )
            else:
                target = VaultTarget.SHARED

            try:
                rel_path = note.path.relative_to(self.vault_path)
            except ValueError:
                continue

            if target == VaultTarget.PERSONAL:
                dest = personal_path / rel_path
                personal_count += 1
            else:
                dest = shared_path / rel_path
                shared_count += 1

            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(note.path, dest)

        self.dual_config.shared_path = shared_path
        self.dual_config.personal_path = personal_path
        config_path = Path("~/.ompa/config.yaml").expanduser()
        self.dual_config.to_yaml(config_path)

        return {
            "shared_notes": shared_count,
            "personal_notes": personal_count,
            "config_saved": str(config_path),
        }
