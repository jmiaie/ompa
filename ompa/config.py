"""
OMPA Configuration — Dual-vault settings and content classification rules.

Supports YAML config file at ~/.ompa/config.yaml or programmatic configuration.
"""

from __future__ import annotations  # defers annotation eval for Python 3.10-3.13

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Ompa

logger = logging.getLogger(__name__)


class IsolationMode(Enum):
    STRICT = "strict"  # Personal never synced to shared; explicit export only
    PERMISSIVE = "permissive"  # Auto-classify with override; export allowed
    MANUAL = "manual"  # Every write requires explicit vault selection


class VaultTarget(Enum):
    SHARED = "shared"
    PERSONAL = "personal"


# Default classification indicators
DEFAULT_SHARED_INDICATORS = [
    "@team",
    "@shared",
    "#shared",
    "#public",
    "decision",
    "spec",
    "agreement",
    "consensus",
]

DEFAULT_PERSONAL_INDICATORS = [
    "@private",
    "#personal",
    "api_key",
    "api-key",
    "token",
    "password",
    "secret",
    "credential",
    "sk-",
    "AKIA",
]

# Folders that always route to shared
SHARED_FOLDERS = {"brain", "org", "work", "perf"}

# Folders that always route to personal
PERSONAL_FOLDERS = {"personal", "private", ".secrets"}


@dataclass
class DualVaultConfig:
    """Configuration for dual-vault architecture."""

    shared_path: Optional[Path] = None
    personal_path: Optional[Path] = None
    isolation_mode: IsolationMode = IsolationMode.STRICT
    default_vault: VaultTarget = VaultTarget.PERSONAL
    prompt_on_ambiguous: bool = True

    shared_indicators: list[str] = field(
        default_factory=lambda: list(DEFAULT_SHARED_INDICATORS)
    )
    personal_indicators: list[str] = field(
        default_factory=lambda: list(DEFAULT_PERSONAL_INDICATORS)
    )

    @property
    def is_dual_vault(self) -> bool:
        """True if both shared and personal vaults are configured."""
        return self.shared_path is not None and self.personal_path is not None

    def classify_content(
        self, content: str, tags: list[str] | None = None, file_path: str | None = None
    ) -> VaultTarget:
        """
        Classify content as shared or personal.

        Checks (in order):
        1. Personal indicators (secrets, credentials) — always personal
        2. Shared indicators (team tags, decision keywords) — always shared
        3. Folder-based rules
        4. Tag-based rules
        5. Default vault
        """
        tags = tags or []
        content_lower = content.lower()
        tags_lower = [t.lower() for t in tags]

        # 1. Personal indicators (check first — safety)
        for indicator in self.personal_indicators:
            if indicator.lower() in content_lower:
                return VaultTarget.PERSONAL
            if indicator.lower() in tags_lower:
                return VaultTarget.PERSONAL

        # 2. Shared indicators
        for indicator in self.shared_indicators:
            if indicator.lower() in content_lower:
                return VaultTarget.SHARED
            if indicator.lower() in tags_lower:
                return VaultTarget.SHARED

        # 3. Folder-based rules
        if file_path:
            path_parts = set(Path(file_path).parts)
            if path_parts & PERSONAL_FOLDERS:
                return VaultTarget.PERSONAL
            if path_parts & SHARED_FOLDERS:
                return VaultTarget.SHARED

        # 4. Default
        return self.default_vault

    def to_yaml(self, config_path: Path) -> None:
        """Save config to a YAML file."""
        try:
            import yaml
        except ImportError:
            logger.warning("PyYAML not installed; cannot save config")
            return

        data = {
            "vaults": {},
            "isolation": {
                "mode": self.isolation_mode.value,
                "default_vault": self.default_vault.value,
                "prompt_on_ambiguous": self.prompt_on_ambiguous,
            },
            "classification": {
                "shared_indicators": self.shared_indicators,
                "personal_indicators": self.personal_indicators,
            },
        }

        vaults: dict[str, Any] = data["vaults"]  # type: ignore[assignment]
        if self.shared_path:
            vaults["shared"] = {
                "path": str(self.shared_path),
                "access": "read-write",
                "auto_classify": True,
            }
        if self.personal_path:
            vaults["personal"] = {
                "path": str(self.personal_path),
                "access": "read-write",
                "auto_classify": True,
                "never_sync_to_shared": True,
            }

        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)


def make_ompa(
    vault_path: str | Path | None = None,
    shared_vault_path: str | Path | None = None,
    personal_vault_path: str | Path | None = None,
    isolation_mode: str = "strict",
    enable_semantic: bool = False,
) -> Ompa:
    """
    Create an Ompa instance, supporting both single and dual vault modes.

    Consolidates instance creation logic used across CLI and MCP server.
    Automatically selects between single-vault and dual-vault initialization.

    Args:
        vault_path: Path for single-vault mode (ignored if dual vault paths provided)
        shared_vault_path: Path to shared vault (activates dual-vault mode)
        personal_vault_path: Path to personal vault (activates dual-vault mode)
        isolation_mode: "strict", "permissive", or "manual"
        enable_semantic: Whether to enable semantic search on initialization

    Returns:
        Ompa instance configured for the selected mode
    """
    from .core import Ompa  # Import here to avoid circular imports

    if shared_vault_path and personal_vault_path:
        # Dual-vault mode
        return Ompa(
            shared_vault_path=shared_vault_path,
            personal_vault_path=personal_vault_path,
            isolation_mode=isolation_mode,
            enable_semantic=enable_semantic,
        )
    # Single-vault mode (legacy / backward compatible)
    return Ompa(
        vault_path=vault_path or Path("."),
        enable_semantic=enable_semantic,
    )
