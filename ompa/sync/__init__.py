"""
OMPA vault sync backends.

Abstracts vault synchronization so the same vault can be synced via
git (default), S3/R2, or rsync — critical for multi-node deployments.

    from ompa.sync import GitSyncBackend, S3SyncBackend, RsyncBackend

    # Git (default — already used internally)
    sync = GitSyncBackend(vault_path="./vault", remote="origin", branch="main")
    sync.push("chore: session wrap-up")

    # S3 / Cloudflare R2
    sync = S3SyncBackend(bucket="my-vault", prefix="ompa/", endpoint_url="https://...")
    sync.push("./vault")

    # rsync over SSH
    sync = RsyncBackend(remote="kai@192.168.1.10:/home/kai/vault")
    sync.push("./vault")
"""

from .base import SyncBackend, SyncResult
from .git import GitSyncBackend
from .rsync import RsyncBackend
from .s3 import S3SyncBackend

__all__ = [
    "SyncBackend",
    "SyncResult",
    "GitSyncBackend",
    "S3SyncBackend",
    "RsyncBackend",
]
