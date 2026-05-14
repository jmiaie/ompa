"""
OMPA vault migration tooling.

Detects the version of an existing vault and applies any needed
schema upgrades: KG index additions, palace rebuilds, semantic index refreshes.

Usage:
    from ompa.migration import VaultMigrator

    migrator = VaultMigrator()
    report = migrator.check("./workspace")
    print(report)

    result = migrator.run("./workspace", dry_run=False)

CLI:
    ao migrate-vault
    ao migrate-vault --vault-path ./my-vault --dry-run
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Increment this when new migrations are added.
CURRENT_SCHEMA_VERSION = 3


@dataclass
class MigrationReport:
    """Summary of what a vault needs before migration runs."""

    vault_path: Path
    detected_version: int
    current_version: int
    needed_migrations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_current(self) -> bool:
        return self.detected_version >= self.current_version

    def __str__(self) -> str:
        lines = [
            f"Vault: {self.vault_path}",
            f"Schema: v{self.detected_version} → v{self.current_version}",
        ]
        if self.is_current:
            lines.append("Status: up to date")
        else:
            lines.append(f"Pending ({len(self.needed_migrations)} migrations):")
            for m in self.needed_migrations:
                lines.append(f"  - {m}")
        for w in self.warnings:
            lines.append(f"  ⚠ {w}")
        return "\n".join(lines)


@dataclass
class MigrationResult:
    """Outcome after running migrations."""

    success: bool
    dry_run: bool
    applied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        prefix = "[DRY RUN] " if self.dry_run else ""
        lines = [f"{prefix}Migration result: {'OK' if self.success else 'FAILED'}"]
        for a in self.applied:
            lines.append(f"  ✓ {a}")
        for s in self.skipped:
            lines.append(f"  - {s} (skipped)")
        for e in self.errors:
            lines.append(f"  ✗ {e}")
        return "\n".join(lines)


class VaultMigrator:
    """
    Detects and applies schema migrations to an OMPA vault.

    Migration steps are additive — each step is checked and only applied
    if the vault hasn't already had it applied.

    Schema version is stored in `.palace/schema_version` (plain text integer).
    """

    def check(self, vault_path: str | Path) -> MigrationReport:
        """
        Inspect the vault and return a migration report without changing anything.

        Args:
            vault_path: Root path of the vault.

        Returns:
            MigrationReport describing current state and needed migrations.
        """
        vault_path = Path(vault_path)
        detected = self._detect_version(vault_path)
        report = MigrationReport(
            vault_path=vault_path,
            detected_version=detected,
            current_version=CURRENT_SCHEMA_VERSION,
        )

        # Collect warnings
        if not vault_path.exists():
            report.warnings.append("Vault directory does not exist")
            return report

        palace_dir = vault_path / ".palace"
        if not palace_dir.exists():
            report.warnings.append(".palace/ not found — run `ao init` first")

        # Check what migrations are needed
        pending = self._pending_migrations(vault_path, detected)
        report.needed_migrations = [m["name"] for m in pending]

        return report

    def run(
        self,
        vault_path: str | Path,
        dry_run: bool = False,
        force: bool = False,
    ) -> MigrationResult:
        """
        Apply pending migrations to the vault.

        Args:
            vault_path: Root path of the vault.
            dry_run: If True, report what would change without modifying anything.
            force: Re-run all migrations even if already applied.

        Returns:
            MigrationResult with applied/skipped/error lists.
        """
        vault_path = Path(vault_path)
        detected = 0 if force else self._detect_version(vault_path)
        pending = self._pending_migrations(vault_path, detected)
        result = MigrationResult(success=True, dry_run=dry_run)

        for migration in pending:
            name = migration["name"]
            try:
                if dry_run:
                    result.applied.append(name)
                    logger.info("[DRY RUN] Would apply: %s", name)
                else:
                    migration["fn"](vault_path)
                    result.applied.append(name)
                    logger.info("Applied migration: %s", name)
            except Exception as e:
                error_msg = f"{name}: {e}"
                result.errors.append(error_msg)
                result.success = False
                logger.error("Migration failed — %s", error_msg)

        if not dry_run and result.success:
            self._write_version(vault_path, CURRENT_SCHEMA_VERSION)

        return result

    # ------------------------------------------------------------------
    # Version detection
    # ------------------------------------------------------------------

    def _detect_version(self, vault_path: Path) -> int:
        version_file = vault_path / ".palace" / "schema_version"
        if version_file.exists():
            try:
                return int(version_file.read_text().strip())
            except (ValueError, OSError):
                pass

        # Heuristic: infer version from what exists
        kg_path = vault_path / ".palace" / "knowledge_graph.sqlite3"
        if not kg_path.exists():
            return 0

        # Check if composite indexes exist (added in v2)
        try:
            conn = sqlite3.connect(str(kg_path))
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
            conn.close()
            index_names = {r[0] for r in rows}
            if "idx_triples_subject_date" in index_names:
                return 2
            return 1
        except Exception:
            return 1

    def _write_version(self, vault_path: Path, version: int) -> None:
        version_file = vault_path / ".palace" / "schema_version"
        version_file.parent.mkdir(parents=True, exist_ok=True)
        version_file.write_text(str(version))

    # ------------------------------------------------------------------
    # Migration definitions
    # ------------------------------------------------------------------

    def _pending_migrations(self, vault_path: Path, from_version: int) -> list[dict]:
        """Return ordered list of migrations not yet applied."""
        all_migrations = [
            {
                "version": 1,
                "name": "v1: initialize .palace/ directory structure",
                "fn": self._m1_init_palace,
            },
            {
                "version": 2,
                "name": "v2: add composite KG indexes (subject_date, object_pred, validity)",
                "fn": self._m2_add_kg_indexes,
            },
            {
                "version": 3,
                "name": "v3: enable WAL mode on knowledge_graph.sqlite3",
                "fn": self._m3_enable_wal,
            },
        ]
        return [m for m in all_migrations if m["version"] > from_version]  # type: ignore[operator]

    def _m1_init_palace(self, vault_path: Path) -> None:
        palace_dir = vault_path / ".palace"
        palace_dir.mkdir(parents=True, exist_ok=True)
        (palace_dir / "wings.json").touch(exist_ok=True)

    def _m2_add_kg_indexes(self, vault_path: Path) -> None:
        kg_path = vault_path / ".palace" / "knowledge_graph.sqlite3"
        if not kg_path.exists():
            return

        conn = sqlite3.connect(str(kg_path))
        try:
            conn.executescript("""
                CREATE INDEX IF NOT EXISTS idx_triples_subject_date
                    ON triples(subject, valid_from);
                CREATE INDEX IF NOT EXISTS idx_triples_object_pred
                    ON triples(object, predicate);
                CREATE INDEX IF NOT EXISTS idx_triples_validity
                    ON triples(valid_from, valid_to);
            """)
            conn.commit()
        finally:
            conn.close()

    def _m3_enable_wal(self, vault_path: Path) -> None:
        kg_path = vault_path / ".palace" / "knowledge_graph.sqlite3"
        if not kg_path.exists():
            return

        conn = sqlite3.connect(str(kg_path))
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.commit()
        finally:
            conn.close()
