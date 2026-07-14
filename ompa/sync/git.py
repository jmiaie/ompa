"""Git sync backend for OMPA vaults."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

from .base import SyncBackend, SyncResult, run_subprocess

logger = logging.getLogger(__name__)


def _git(args: list[str], cwd: Path, timeout: int = 30) -> tuple[int, str, str]:
    """Run a git command. Returns (returncode, stdout, stderr)."""
    git_path = shutil.which("git")
    if not git_path:
        return 1, "", "git not found in PATH"
    return run_subprocess([git_path, *args], cwd=cwd, timeout=timeout, label=f"git {args[0]}")


class GitSyncBackend(SyncBackend):
    """
    Git-based vault sync (add → commit → push / pull).

    This is the default OMPA sync method and the one used internally
    by the existing ao sync CLI command.

    Example:
        from ompa.sync import GitSyncBackend

        sync = GitSyncBackend(remote="origin", branch="main")
        result = sync.push("./vault", message="chore: session wrap-up")
        result = sync.pull("./vault")
        result = sync.status("./vault")
    """

    def __init__(
        self,
        remote: str = "origin",
        branch: str = "main",
        author_name: str = "OMPA",
        author_email: str = "ompa@local",
        add_pattern: str = ".",
    ):
        self.remote = remote
        self.branch = branch
        self.author_name = author_name
        self.author_email = author_email
        self.add_pattern = add_pattern

    @property
    def name(self) -> str:
        return "git"

    def push(self, vault_path: Path, message: str = "chore: vault sync") -> SyncResult:
        vault_path = Path(vault_path)
        env_extras = {
            "GIT_AUTHOR_NAME": self.author_name,
            "GIT_AUTHOR_EMAIL": self.author_email,
            "GIT_COMMITTER_NAME": self.author_name,
            "GIT_COMMITTER_EMAIL": self.author_email,
        }

        # Stage changes
        rc, _, err = _git(["add", self.add_pattern], vault_path)
        if rc != 0:
            return SyncResult(success=False, backend=self.name, direction="push", error=f"git add failed: {err}")

        # Check if there's anything to commit
        rc, stdout, _ = _git(["status", "--porcelain"], vault_path)
        if not stdout.strip():
            return SyncResult(success=True, backend=self.name, direction="push", message="nothing to commit", files_changed=0)

        files_changed = len([l for l in stdout.splitlines() if l.strip()])

        # Commit
        rc, _, err = _git(["commit", "-m", message or "chore: vault sync"], vault_path)
        if rc != 0:
            return SyncResult(success=False, backend=self.name, direction="push", error=f"git commit failed: {err}")

        # Push
        rc, _, err = _git(["push", self.remote, self.branch], vault_path)
        if rc != 0:
            return SyncResult(success=False, backend=self.name, direction="push", error=f"git push failed: {err}")

        logger.info("Git push: %d files → %s/%s", files_changed, self.remote, self.branch)
        return SyncResult(
            success=True,
            backend=self.name,
            direction="push",
            files_changed=files_changed,
            message=f"pushed to {self.remote}/{self.branch}",
        )

    def pull(self, vault_path: Path) -> SyncResult:
        vault_path = Path(vault_path)
        rc, stdout, err = _git(["pull", "--rebase", self.remote, self.branch], vault_path)
        if rc != 0:
            return SyncResult(success=False, backend=self.name, direction="pull", error=err)

        files_changed = len([l for l in stdout.splitlines() if l.strip() and not l.startswith("Already")])
        return SyncResult(
            success=True,
            backend=self.name,
            direction="pull",
            files_changed=files_changed,
            message=stdout[:120] if stdout else "up to date",
        )

    def status(self, vault_path: Path) -> SyncResult:
        vault_path = Path(vault_path)
        rc, stdout, err = _git(["status", "--porcelain"], vault_path)
        if rc != 0:
            return SyncResult(success=False, backend=self.name, direction="status", error=err)

        lines = [l for l in stdout.splitlines() if l.strip()]
        return SyncResult(
            success=True,
            backend=self.name,
            direction="status",
            files_changed=len(lines),
            message=f"{len(lines)} uncommitted changes" if lines else "clean",
            details={"uncommitted": lines},
        )
