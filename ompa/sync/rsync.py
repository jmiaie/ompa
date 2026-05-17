"""rsync sync backend for OMPA vaults — ideal for LAN / Tailscale multi-node."""

from __future__ import annotations

import logging
import shutil
import subprocess  # noqa: S404
from pathlib import Path

from .base import SyncBackend, SyncResult

logger = logging.getLogger(__name__)

# Files/dirs to exclude from rsync (mirrors .gitignore conventions)
_DEFAULT_EXCLUDES = [
    ".venv/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    ".ruff_cache/",
    ".agent-memory/",
    ".internal/",
    "semantic_index/",  # large model cache — rebuild on remote
]


class RsyncBackend(SyncBackend):
    """
    rsync-based vault sync over SSH — optimized for Tailscale LAN deployments.

    Ideal for the Jarv/Kai/Tai multi-node topology where nodes share a vault
    over Tailscale without needing a git remote or cloud storage.

    Requires: rsync installed on both local and remote hosts.

    Example:
        from ompa.sync import RsyncBackend

        # Push local vault to Kai over Tailscale
        sync = RsyncBackend(remote="kai@100.x.x.x:/home/kai/ompa-vault")
        result = sync.push("./vault")

        # With SSH key
        sync = RsyncBackend(
            remote="kai@100.x.x.x:/home/kai/ompa-vault",
            ssh_key="~/.ssh/id_ed25519",
        )

        # Bidirectional: pull from Tai first, then push
        sync = RsyncBackend(remote="tai@100.x.x.x:/home/tai/vault")
        sync.pull("./vault")   # receive Tai's changes
        sync.push("./vault")   # send local changes to Tai
    """

    def __init__(
        self,
        remote: str,
        ssh_key: str | None = None,
        ssh_port: int = 22,
        excludes: list[str] | None = None,
        compress: bool = True,
        delete: bool = False,         # True = exact mirror (destructive)
        dry_run_on_status: bool = True,
    ):
        self.remote = remote
        self.ssh_key = ssh_key
        self.ssh_port = ssh_port
        self.excludes = excludes if excludes is not None else _DEFAULT_EXCLUDES
        self.compress = compress
        self.delete = delete
        self.dry_run_on_status = dry_run_on_status

    @property
    def name(self) -> str:
        return "rsync"

    def _build_rsync_cmd(
        self,
        src: str,
        dst: str,
        dry_run: bool = False,
    ) -> list[str]:
        rsync = shutil.which("rsync")
        if not rsync:
            raise RuntimeError("rsync not found in PATH")

        cmd = [rsync, "-av"]
        if self.compress:
            cmd.append("-z")
        if dry_run:
            cmd.append("--dry-run")
        if self.delete:
            cmd.append("--delete")

        # SSH options
        ssh_opts = [f"-p {self.ssh_port}"]
        if self.ssh_key:
            ssh_opts.append(f"-i {self.ssh_key}")
        cmd += ["-e", "ssh " + " ".join(ssh_opts)]

        # Excludes
        for excl in self.excludes:
            cmd += ["--exclude", excl]

        cmd += [src, dst]
        return cmd

    def _run(self, cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
        try:
            result = subprocess.run(  # noqa: S603
                cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except subprocess.TimeoutExpired:
            return 1, "", f"rsync timed out after {timeout}s"
        except Exception as e:
            return 1, "", str(e)

    def push(self, vault_path: Path, message: str = "") -> SyncResult:
        vault_path = Path(vault_path)
        src = str(vault_path).rstrip("/\\") + "/"   # trailing slash = contents
        dst = self.remote.rstrip("/") + "/"

        try:
            cmd = self._build_rsync_cmd(src, dst)
        except RuntimeError as e:
            return SyncResult(success=False, backend=self.name, direction="push", error=str(e))

        rc, stdout, err = self._run(cmd)
        if rc != 0:
            return SyncResult(success=False, backend=self.name, direction="push", error=err)

        transferred = len([ln for ln in stdout.splitlines() if ln and not ln.startswith("sending")])
        logger.info("rsync push: %d files → %s", transferred, self.remote)
        return SyncResult(
            success=True,
            backend=self.name,
            direction="push",
            files_changed=transferred,
            message=f"synced to {self.remote}",
        )

    def pull(self, vault_path: Path) -> SyncResult:
        vault_path = Path(vault_path)
        src = self.remote.rstrip("/") + "/"
        dst = str(vault_path).rstrip("/\\") + "/"

        try:
            cmd = self._build_rsync_cmd(src, dst)
        except RuntimeError as e:
            return SyncResult(success=False, backend=self.name, direction="pull", error=str(e))

        rc, stdout, err = self._run(cmd)
        if rc != 0:
            return SyncResult(success=False, backend=self.name, direction="pull", error=err)

        transferred = len([ln for ln in stdout.splitlines() if ln and not ln.startswith("receiving")])
        return SyncResult(
            success=True,
            backend=self.name,
            direction="pull",
            files_changed=transferred,
            message=f"received from {self.remote}",
        )

    def status(self, vault_path: Path) -> SyncResult:
        vault_path = Path(vault_path)
        src = str(vault_path).rstrip("/\\") + "/"
        dst = self.remote.rstrip("/") + "/"

        try:
            cmd = self._build_rsync_cmd(src, dst, dry_run=True)
        except RuntimeError as e:
            return SyncResult(success=False, backend=self.name, direction="status", error=str(e))

        rc, stdout, err = self._run(cmd, timeout=30)
        if rc != 0:
            return SyncResult(success=False, backend=self.name, direction="status", error=err)

        would_transfer = [ln for ln in stdout.splitlines() if ln and not ln.startswith(("sending", "sent", "total"))]
        return SyncResult(
            success=True,
            backend=self.name,
            direction="status",
            files_changed=len(would_transfer),
            message=f"{len(would_transfer)} files would be synced to {self.remote}",
            details={"would_transfer": would_transfer[:20]},
        )
