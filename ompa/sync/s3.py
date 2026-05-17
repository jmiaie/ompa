"""S3 / Cloudflare R2 sync backend for OMPA vaults."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .base import SyncBackend, SyncResult

logger = logging.getLogger(__name__)


class S3SyncBackend(SyncBackend):
    """
    S3-compatible vault sync (works with AWS S3, Cloudflare R2, MinIO, etc.).

    Requires: pip install ompa[s3]  (adds boto3)

    Uses a simple mirror strategy:
        push — upload all vault .md files and .palace/ to S3
        pull — download all S3 objects under the prefix to vault_path

    Example:
        from ompa.sync import S3SyncBackend

        # AWS S3
        sync = S3SyncBackend(bucket="my-vault-bucket", prefix="ompa/")

        # Cloudflare R2 (S3-compatible)
        sync = S3SyncBackend(
            bucket="my-vault",
            prefix="ompa/",
            endpoint_url="https://<account-id>.r2.cloudflarestorage.com",
            aws_access_key_id="...",
            aws_secret_access_key="...",
        )

        result = sync.push("./vault")
        result = sync.pull("./vault")
    """

    def __init__(
        self,
        bucket: str,
        prefix: str = "ompa-vault/",
        endpoint_url: str | None = None,
        region_name: str = "auto",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        include_palace: bool = True,
        storage_class: str = "STANDARD",
    ):
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/"
        self.endpoint_url = endpoint_url
        self.region_name = region_name
        self.aws_access_key_id = aws_access_key_id or os.environ.get("AWS_ACCESS_KEY_ID")
        self.aws_secret_access_key = aws_secret_access_key or os.environ.get("AWS_SECRET_ACCESS_KEY")
        self.include_palace = include_palace
        self.storage_class = storage_class
        self._client = None

    @property
    def name(self) -> str:
        return "s3"

    def _get_client(self):
        if self._client is None:
            try:
                import boto3
            except ImportError as exc:
                raise ImportError(
                    "boto3 is required for the S3 backend. "
                    "Install with: pip install ompa[s3]"
                ) from exc
            kwargs = {"region_name": self.region_name}
            if self.endpoint_url:
                kwargs["endpoint_url"] = self.endpoint_url
            if self.aws_access_key_id:
                kwargs["aws_access_key_id"] = self.aws_access_key_id
            if self.aws_secret_access_key:
                kwargs["aws_secret_access_key"] = self.aws_secret_access_key
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _collect_files(self, vault_path: Path) -> list[Path]:
        """Collect all files to sync from the vault."""
        files = []
        for f in vault_path.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(vault_path)
            parts = rel.parts
            # Always include .md files; include .palace/ if enabled
            if f.suffix == ".md":
                files.append(f)
            elif self.include_palace and parts and parts[0] == ".palace" and "semantic_index" not in str(rel):
                # Skip large binary/model files in semantic_index
                files.append(f)
        return files

    def push(self, vault_path: Path, message: str = "") -> SyncResult:
        vault_path = Path(vault_path)
        client = self._get_client()
        files = self._collect_files(vault_path)
        uploaded = 0
        errors = []

        for f in files:
            key = self.prefix + str(f.relative_to(vault_path)).replace("\\", "/")
            try:
                client.upload_file(
                    str(f),
                    self.bucket,
                    key,
                    ExtraArgs={"StorageClass": self.storage_class},
                )
                uploaded += 1
            except Exception as e:
                errors.append(f"{f.name}: {e}")
                logger.warning("S3 upload failed for %s: %s", f, e)

        if errors:
            return SyncResult(
                success=False,
                backend=self.name,
                direction="push",
                files_changed=uploaded,
                error=f"{len(errors)} upload errors",
                details={"errors": errors},
            )

        logger.info("S3 push: %d files → s3://%s/%s", uploaded, self.bucket, self.prefix)
        return SyncResult(
            success=True,
            backend=self.name,
            direction="push",
            files_changed=uploaded,
            message=f"uploaded {uploaded} files to s3://{self.bucket}/{self.prefix}",
        )

    def pull(self, vault_path: Path) -> SyncResult:
        vault_path = Path(vault_path)
        client = self._get_client()
        downloaded = 0
        errors = []

        try:
            paginator = client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.bucket, Prefix=self.prefix)

            for page in pages:
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    rel_key = key[len(self.prefix):]
                    if not rel_key:
                        continue

                    local_path = vault_path / rel_key.replace("/", os.sep)
                    local_path.parent.mkdir(parents=True, exist_ok=True)

                    try:
                        client.download_file(self.bucket, key, str(local_path))
                        downloaded += 1
                    except Exception as e:
                        errors.append(f"{rel_key}: {e}")
                        logger.warning("S3 download failed for %s: %s", key, e)

        except Exception as e:
            return SyncResult(success=False, backend=self.name, direction="pull", error=str(e))

        if errors:
            return SyncResult(
                success=False,
                backend=self.name,
                direction="pull",
                files_changed=downloaded,
                error=f"{len(errors)} download errors",
                details={"errors": errors},
            )

        return SyncResult(
            success=True,
            backend=self.name,
            direction="pull",
            files_changed=downloaded,
            message=f"downloaded {downloaded} files from s3://{self.bucket}/{self.prefix}",
        )

    def status(self, vault_path: Path) -> SyncResult:
        """List objects in the S3 prefix without downloading."""
        vault_path = Path(vault_path)
        client = self._get_client()

        try:
            paginator = client.get_paginator("list_objects_v2")
            pages = paginator.paginate(Bucket=self.bucket, Prefix=self.prefix)
            remote_keys = []
            for page in pages:
                for obj in page.get("Contents", []):
                    remote_keys.append(obj["Key"])

            local_files = self._collect_files(vault_path)

            return SyncResult(
                success=True,
                backend=self.name,
                direction="status",
                files_changed=0,
                message=f"{len(remote_keys)} remote objects, {len(local_files)} local files",
                details={"remote_count": len(remote_keys), "local_count": len(local_files)},
            )
        except Exception as e:
            return SyncResult(success=False, backend=self.name, direction="status", error=str(e))
