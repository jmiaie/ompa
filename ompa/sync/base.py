"""Abstract base class for OMPA vault sync backends."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class SyncResult:
    """Result of a sync operation."""

    success: bool
    backend: str
    direction: str              # "push" | "pull" | "status"
    files_changed: int = 0
    message: str = ""
    error: str | None = None
    details: dict = field(default_factory=dict)

    def __str__(self) -> str:
        if self.success:
            return f"[{self.backend}] {self.direction}: {self.files_changed} files — {self.message}"
        return f"[{self.backend}] {self.direction} FAILED: {self.error}"


class SyncBackend(ABC):
    """
    Abstract base class for vault sync backends.

    All backends implement three operations:
        push(vault_path, message)  — send local → remote
        pull(vault_path)           — receive remote → local
        status(vault_path)         — inspect sync state without modifying
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this backend (e.g. 'git', 's3', 'rsync')."""

    @abstractmethod
    def push(self, vault_path: Path, message: str = "") -> SyncResult:
        """
        Push local vault changes to the remote.

        Args:
            vault_path: Local vault root directory.
            message: Commit/sync message (used by git backend).

        Returns:
            SyncResult with outcome details.
        """

    @abstractmethod
    def pull(self, vault_path: Path) -> SyncResult:
        """
        Pull remote changes into the local vault.

        Args:
            vault_path: Local vault root directory.

        Returns:
            SyncResult with outcome details.
        """

    @abstractmethod
    def status(self, vault_path: Path) -> SyncResult:
        """
        Check sync state without modifying anything.

        Returns:
            SyncResult describing what would change on push/pull.
        """
