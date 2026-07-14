"""Abstract base class for OMPA vault sync backends."""

from __future__ import annotations

import subprocess  # noqa: S404
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


def run_subprocess(
    cmd: list[str],
    cwd: Path | None = None,
    timeout: int = 30,
    label: str | None = None,
) -> tuple[int, str, str]:
    """
    Run a subprocess command, capturing stdout/stderr as text.

    Shared by the git and rsync backends, which otherwise duplicated this
    exact subprocess.run + timeout/exception handling.

    Returns (returncode, stdout, stderr). On timeout or other exceptions,
    returns (1, "", <error message>) instead of raising.
    """
    try:
        result = subprocess.run(  # noqa: S603
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return result.returncode, result.stdout.strip(), result.stderr.strip()
    except subprocess.TimeoutExpired:
        what = label or cmd[0]
        return 1, "", f"{what} timed out after {timeout}s"
    except Exception as e:
        return 1, "", str(e)


@dataclass
class SyncResult:
    """Result of a sync operation."""

    success: bool
    backend: str
    direction: str              # "push" | "pull" | "status"
    files_changed: int = 0
    message: str = ""
    error: str | None = None
    # Backend-specific extras (e.g. {"uncommitted": [...]}, {"errors": [...]});
    # shape varies per backend, so this stays a loosely-typed bag by design.
    details: dict[str, Any] = field(default_factory=dict)

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
