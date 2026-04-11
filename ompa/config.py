"""
OMPA Configuration — Dual-vault settings and content classification rules.

Supports YAML config file at ~/.ompa/config.yaml or programmatic configuration.
"""

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

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
        self, content: str, tags: list[str] = None, file_path: str = None
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

    @classmethod
    def from_yaml(cls, config_path: Path) -> "DualVaultConfig":
        """Load config from a YAML file."""
        config = cls()

        if not config_path.exists():
            return config

        try:
            import yaml

            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}

            vaults = data.get("vaults", {})
            if "shared" in vaults and "path" in vaults["shared"]:
                config.shared_path = Path(vaults["shared"]["path"]).expanduser()
            if "personal" in vaults and "path" in vaults["personal"]:
                config.personal_path = Path(vaults["personal"]["path"]).expanduser()

            isolation = data.get("isolation", {})
            mode_str = isolation.get("mode", "strict")
            config.isolation_mode = IsolationMode(mode_str)
            default_str = isolation.get("default_vault", "personal")
            config.default_vault = VaultTarget(default_str)
            config.prompt_on_ambiguous = isolation.get("prompt_on_ambiguous", True)

            classification = data.get("classification", {})
            if "shared_indicators" in classification:
                config.shared_indicators = classification["shared_indicators"]
            if "personal_indicators" in classification:
                config.personal_indicators = classification["personal_indicators"]

        except ImportError:
            logger.debug("PyYAML not installed; using default config")
        except Exception as e:
            logger.warning("Error loading config from %s: %s", config_path, e)

        return config

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

        if self.shared_path:
            data["vaults"]["shared"] = {
                "path": str(self.shared_path),
                "access": "read-write",
                "auto_classify": True,
            }
        if self.personal_path:
            data["vaults"]["personal"] = {
                "path": str(self.personal_path),
                "access": "read-write",
                "auto_classify": True,
                "never_sync_to_shared": True,
            }

        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(data, f, default_flow_style=False, sort_keys=False)
