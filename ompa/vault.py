"""
Vault management for OMPA.
Handles note organization, templates, wikilinks, and frontmatter validation.
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

import frontmatter
import yaml

# Prefer libyaml's C extension — 3-5× faster than the pure-Python loader on
# the small frontmatter blocks OMPA notes carry. Falls back silently on
# installs that didn't build libyaml.
try:
    from yaml import CSafeLoader as _YamlLoader  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - depends on libyaml availability
    from yaml import SafeLoader as _YamlLoader

logger = logging.getLogger(__name__)


def _fast_parse_frontmatter(text: str) -> tuple[dict, str]:
    """Split a note into (frontmatter_dict, body).

    Hand-rolled splitter + CSafeLoader is ~4× faster than
    ``frontmatter.load`` on the typical OMPA note (small YAML block, short
    body). Falls back to ``{}`` + raw text when the block isn't well-formed
    so the caller can decide whether to retry with the slow parser.
    """
    if not text.startswith("---"):
        return {}, text
    # Require the opening fence to be followed by a newline (covers --- \n
    # and ---\r\n). Bail out if the file just happens to start with ---.
    after = text[3:]
    if not after.startswith(("\n", "\r\n")):
        return {}, text
    newline_len = 2 if after.startswith("\r\n") else 1
    body_start = 3 + newline_len
    # Find the closing fence on its own line.
    end = text.find("\n---", body_start - 1)
    while end != -1:
        tail_start = end + 4
        # Accept EOF or a following newline (handles trailing \r).
        if tail_start >= len(text) or text[tail_start] in ("\n", "\r"):
            break
        end = text.find("\n---", tail_start)
    if end == -1:
        return {}, text
    yaml_block = text[body_start:end]
    # Skip the closing fence and the newline that follows it (if any).
    rest_start = end + 4
    if rest_start < len(text) and text[rest_start] == "\r":
        rest_start += 1
    if rest_start < len(text) and text[rest_start] == "\n":
        rest_start += 1
    body = text[rest_start:]
    try:
        meta = yaml.load(yaml_block, Loader=_YamlLoader) or {}
    except yaml.YAMLError:
        return {}, text
    if not isinstance(meta, dict):
        return {}, text
    return meta, body


# Shared exclude patterns for vault traversal
DEFAULT_EXCLUDE_PATTERNS = [".git", ".claude", "thinking"]

# Precompiled once at import. The KG layer reuses this via
# :func:`extract_wikilinks` so we don't have two copies of the pattern
# drifting apart.
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")


def extract_wikilinks(text: str) -> list[str]:
    """Extract ``[[wikilinks]]`` from text and normalize each target.

    Normalization:
      * ``[[target|display]]`` — drop the display text, keep ``target``.
      * ``[[target.md]]`` — drop a trailing ``.md`` extension (it gets
        re-added by the resolver that turns the wikilink back into a Path).
      * Leading/trailing whitespace stripped.
      * Empty targets dropped.

    Shared between :class:`Note` and :mod:`ompa.knowledge_graph` so link
    parsing stays consistent across the codebase.
    """
    normalized: list[str] = []
    for link in _WIKILINK_RE.findall(text):
        target = link.split("|")[0].strip()
        if target.lower().endswith(".md"):
            target = target[:-3]
        if target:
            normalized.append(target)
    return normalized


def _safe_resolve(base: Path, untrusted: str) -> Path:
    """
    Resolve an untrusted path relative to a base directory.
    Raises ValueError if the resolved path escapes the base.
    """
    resolved = (base / untrusted).resolve()
    base_resolved = base.resolve()
    try:
        resolved.relative_to(base_resolved)
    except ValueError as e:
        raise ValueError(f"Path traversal blocked: {untrusted!r} escapes {base}") from e
    return resolved


@dataclass
class VaultConfig:
    vault_path: Path
    brain_folder: Path | None = None
    work_folder: Path | None = None
    org_folder: Path | None = None
    perf_folder: Path | None = None
    thinking_folder: Path | None = None
    templates_folder: Path | None = None

    def __post_init__(self):
        if self.brain_folder is None:
            self.brain_folder = self.vault_path / "brain"
        if self.work_folder is None:
            self.work_folder = self.vault_path / "work"
        if self.org_folder is None:
            self.org_folder = self.vault_path / "org"
        if self.perf_folder is None:
            self.perf_folder = self.vault_path / "perf"
        if self.thinking_folder is None:
            self.thinking_folder = self.vault_path / "thinking"
        if self.templates_folder is None:
            self.templates_folder = self.vault_path / "templates"


@dataclass
class Note:
    path: Path
    frontmatter: dict[str, object] = field(default_factory=dict)
    content: str = ""
    links: list[str] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: Path) -> "Note":
        """Load a note from a file."""
        if not path.exists():
            return cls(path=path)

        # Fast path: single read + CSafeLoader. Falls back to
        # python-frontmatter on anything the hand-rolled splitter can't
        # confidently handle (e.g. alternate fence styles, TOML frontmatter).
        try:
            text = path.read_text(encoding="utf-8")
        except Exception as e:
            logger.debug("Could not read %s: %s", path, e)
            return cls(path=path)

        try:
            meta, body = _fast_parse_frontmatter(text)
            content = body.strip()
            return cls(
                path=path,
                frontmatter=meta,
                content=content,
                links=cls._extract_wikilinks(content),
            )
        except Exception as e:
            logger.debug("Fast parse failed for %s: %s", path, e)
            try:
                post = frontmatter.loads(text)
                content = post.content.strip()
                return cls(
                    path=path,
                    frontmatter=dict(post.metadata),
                    content=content,
                    links=cls._extract_wikilinks(content),
                )
            except Exception as e2:
                logger.debug("Frontmatter fallback failed for %s: %s", path, e2)
                return cls(path=path, content=text, links=cls._extract_wikilinks(text))

    @staticmethod
    def _extract_wikilinks(text: str) -> list[str]:
        """Extract [[wikilinks]] from text, normalizing targets.

        Delegates to the module-level :func:`extract_wikilinks` so the KG
        layer and any other caller share one implementation.
        """
        return extract_wikilinks(text)

    def has_links(self) -> bool:
        """Check if note has any wikilinks."""
        return len(self.links) > 0

    def save(self) -> None:
        """Save note to file with frontmatter."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(self.content, **self.frontmatter)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(frontmatter.dumps(post))


class Vault:
    """Manages the OMPA vault structure."""

    # Folder structure
    STRUCTURE: ClassVar[dict[str, list[str]]] = {
        "brain": [
            "Memories.md",
            "Key Decisions.md",
            "Patterns.md",
            "Gotchas.md",
            "Skills.md",
            "North Star.md",
        ],
        "work/active": [],
        "work/archive": [],
        "work/incidents": [],
        "work/1-1": [],
        "org/people": [],
        "org/teams": [],
        "perf/competencies": [],
        "perf/brag": [],
        "thinking": [],
        "templates": [
            "Work Note.md",
            "Decision Record.md",
            "1-1 Meeting.md",
            "Incident.md",
            "Thinking Note.md",
        ],
    }

    def __init__(self, vault_path: str | Path):
        self.vault_path = Path(vault_path).resolve()
        self.config = VaultConfig(vault_path=self.vault_path)
        self._ensure_structure()
        # Note cache: path -> (mtime, Note). Entries are reused across
        # `list_notes` / `find_orphans` / `get_stats` / `search_by_name`
        # as long as the on-disk mtime matches what we cached. Writes should
        # call `invalidate_path` (or `invalidate_cache` for bulk operations).
        self._note_cache: dict[Path, tuple[float, Note]] = {}

    def _ensure_structure(self) -> None:
        """Create vault folder structure if it doesn't exist."""
        for folder in self.STRUCTURE:
            folder_path = self.vault_path / folder
            folder_path.mkdir(parents=True, exist_ok=True)

    def invalidate_cache(self) -> None:
        """Drop the entire note cache. Call after bulk operations
        (sync, rebuild-index, migration) where many notes change at once."""
        self._note_cache.clear()

    def invalidate_path(self, path: str | Path) -> None:
        """Drop a single entry from the note cache. Call after writing
        or editing a specific note so the next scan re-parses it."""
        try:
            resolved = Path(path).resolve()
        except (OSError, ValueError):
            return
        self._note_cache.pop(resolved, None)

    def list_notes(self, exclude_patterns: list[str] | None = None) -> list[Note]:
        """List all markdown notes in the vault.

        Uses an mtime-keyed cache: notes whose on-disk mtime hasn't changed
        since the last scan are returned from memory rather than re-parsed.
        """
        exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS
        notes: list[Note] = []
        seen: set[Path] = set()

        for path in self.vault_path.rglob("*.md"):
            # Check exclusions
            if any(excl in str(path) for excl in exclude_patterns):
                continue
            try:
                mtime = path.stat().st_mtime
            except OSError:
                # File disappeared between rglob and stat — skip.
                continue
            seen.add(path)
            cached = self._note_cache.get(path)
            if cached is not None and cached[0] == mtime:
                notes.append(cached[1])
            else:
                note = Note.from_file(path)
                self._note_cache[path] = (mtime, note)
                notes.append(note)

        # Evict cache entries for paths that no longer exist or are now excluded.
        if len(self._note_cache) > len(seen):
            for stale in list(self._note_cache.keys()):
                if stale not in seen:
                    del self._note_cache[stale]

        return notes

    def _build_filename_index(self, notes: list[Note]) -> dict[str, Path]:
        """Build a case-insensitive filename → path index for wikilink resolution."""
        index = {}
        for note in notes:
            # Index by stem (without .md) — case-insensitive
            key = note.path.stem.lower()
            index[key] = note.path
            # Also index by full filename
            index[note.path.name.lower()] = note.path
        return index

    def _resolve_wikilink(
        self, link: str, filename_index: dict[str, Path]
    ) -> Path | None:
        """Resolve a wikilink to a file path using multiple strategies."""
        link_lower = link.lower()

        # 1. Direct filename match (case-insensitive)
        if link_lower in filename_index:
            return filename_index[link_lower]

        # 2. Try with .md extension stripped
        if link_lower.endswith(".md"):
            stem = link_lower[:-3]
            if stem in filename_index:
                return filename_index[stem]

        # 3. Try exact path from vault root
        direct = self.vault_path / link
        if direct.exists():
            return direct
        with_ext = self.vault_path / f"{link}.md"
        if with_ext.exists():
            return with_ext

        return None

    def find_orphans(self) -> list[Note]:
        """Find notes with no incoming links from other notes."""
        all_notes = self.list_notes()
        filename_index = self._build_filename_index(all_notes)

        # Collect unique link targets once — the same wikilink often appears
        # across many notes, and each resolve can do a filesystem `.exists()`
        # call. Deduping avoids O(links) disk stats during full-vault scans.
        unique_links: set[str] = set()
        for note in all_notes:
            unique_links.update(note.links)

        linked_files: set[Path] = set()
        for link in unique_links:
            resolved = self._resolve_wikilink(link, filename_index)
            if resolved:
                linked_files.add(resolved)

        return [
            n
            for n in all_notes
            if n.path not in linked_files
            and n.path.name not in ["Home.md", "README.md"]
        ]

    def search_by_name(self, query: str) -> list[Note]:
        """Search notes by filename."""
        query_lower = query.lower()
        return [n for n in self.list_notes() if query_lower in n.path.stem.lower()]

    def get_brain_note(self, name: str) -> Note | None:
        """Get a brain note by name. Name is sanitized to prevent path traversal."""
        # Reject names with path separators or parent-dir references
        if "/" in name or "\\" in name or ".." in name:
            raise ValueError(f"Invalid brain note name: {name!r}")
        safe_name = Path(name).name  # Strip any directory components
        path = self.config.brain_folder / f"{safe_name}.md"
        path = path.resolve()
        # Ensure we stay within brain folder
        try:
            path.relative_to(self.config.brain_folder.resolve())
        except ValueError as e:
            raise ValueError(f"Invalid brain note name: {name!r}") from e
        if path.exists():
            return Note.from_file(path)
        return None

    def update_brain_note(self, name: str, content: str, append: bool = False) -> None:
        """Update a brain note. Name is sanitized to prevent path traversal."""
        # Reject names with path separators or parent-dir references
        if "/" in name or "\\" in name or ".." in name:
            raise ValueError(f"Invalid brain note name: {name!r}")
        safe_name = Path(name).name  # Strip any directory components
        path = self.config.brain_folder / f"{safe_name}.md"
        path = path.resolve()
        try:
            path.relative_to(self.config.brain_folder.resolve())
        except ValueError as e:
            raise ValueError(f"Invalid brain note name: {name!r}") from e
        path.parent.mkdir(parents=True, exist_ok=True)

        if append and path.exists():
            note = Note.from_file(path)
            note.content += f"\n{content}"
        else:
            note = Note(path=path, content=content)

        note.save()
        self.invalidate_path(path)

    def create_from_template(
        self, template_name: str, target_name: str, **kwargs
    ) -> Note:
        """Create a new note from a template. Both names are sanitized."""
        # Sanitize template name
        safe_template = Path(template_name).name
        template_path = self.config.templates_folder / f"{safe_template}.md"
        if not template_path.exists():
            raise FileNotFoundError(f"Template not found: {template_name}")

        template = Note.from_file(template_path)

        # Replace placeholders
        content = template.content
        for key, value in kwargs.items():
            content = content.replace(f"{{{{{key}}}}}", str(value))

        # Sanitize and validate target path
        target_path = _safe_resolve(self.vault_path, target_name)
        note = Note(path=target_path, frontmatter=template.frontmatter, content=content)
        note.save()
        self.invalidate_path(target_path)
        return note

    def get_stats(self) -> dict:
        """Get vault statistics.

        Returns:
            dict with keys:
                total_notes (int): Markdown files in the vault.
                orphans (int): Notes with no incoming wikilinks.
                folder_counts (dict[str, int]): Note count per folder.
                brain_notes (int): Notes in the ``brain/`` folder or
                    with ``wing: brain`` in frontmatter.
        """
        notes = self.list_notes()
        filename_index = self._build_filename_index(notes)

        # Build linked set using smart wikilink resolution. Dedup targets
        # first so each unique link is resolved once (avoids redundant
        # filesystem `.exists()` calls for heavily-referenced notes).
        unique_links: set[str] = set()
        for note in notes:
            unique_links.update(note.links)

        linked_files: set[Path] = set()
        for link in unique_links:
            resolved = self._resolve_wikilink(link, filename_index)
            if resolved:
                linked_files.add(resolved)

        orphan_count = sum(
            1
            for n in notes
            if n.path not in linked_files
            and n.path.name not in ["Home.md", "README.md"]
        )

        folder_counts = {}
        brain_count = 0
        for note in notes:
            folder = note.path.parent.name or "root"
            folder_counts[folder] = folder_counts.get(folder, 0) + 1

            # Count brain notes: in brain/ folder OR wing=brain in frontmatter
            if "brain" in note.path.parts or note.frontmatter.get("wing", "").lower() == "brain":
                brain_count += 1

        # Also count brain folder files not yet in notes list (e.g., empty ones)
        if self.config.brain_folder.exists():
            folder_brain = len(list(self.config.brain_folder.glob("*.md")))
            brain_count = max(brain_count, folder_brain)

        return {
            "total_notes": len(notes),
            "orphans": orphan_count,
            "folder_counts": folder_counts,
            "brain_notes": brain_count,
        }

    def validate_write(self, file_path: str) -> dict:
        """Validate a markdown file for frontmatter and wikilinks.

        File must be within the vault directory.

        Returns:
            dict with keys:
                valid (bool): ``True`` if the note passes all checks.
                warnings (list[str]): Human-readable issues found
                    (missing frontmatter fields, no wikilinks, etc.).
        """
        try:
            path = _safe_resolve(self.vault_path, file_path)
        except ValueError:
            # Also handle absolute paths that are within the vault
            path = Path(file_path).resolve()
            try:
                path.relative_to(self.vault_path)
            except ValueError:
                return {"valid": False, "warnings": ["Path is outside the vault"]}

        warnings = []
        valid = True

        if not path.exists():
            return {"valid": False, "warnings": ["File does not exist"]}

        if path.suffix != ".md":
            return {"valid": True, "warnings": []}

        # Skip dotfiles and template files
        if path.name.startswith(".") or path.name.startswith("README."):
            return {"valid": True, "warnings": []}

        if (
            "templates" in path.parts
            or "thinking" in path.parts
            or ".claude" in path.parts
        ):
            return {"valid": True, "warnings": []}

        try:
            content = path.read_text(encoding="utf-8")

            # Check frontmatter
            if not content.startswith("---"):
                warnings.append("Missing YAML frontmatter")
                valid = False
            else:
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    fm = parts[1]
                    if "date:" not in fm and "date :" not in fm:
                        warnings.append("Missing 'date' in frontmatter")
                    if "description:" not in fm and "description :" not in fm:
                        warnings.append(
                            "Missing 'description' in frontmatter (~150 chars)"
                        )
                    if "tags:" not in fm and "tags :" not in fm:
                        warnings.append("Missing 'tags' in frontmatter")

            # Check wikilinks (skip very short notes)
            if len(content) > 300 and "[[" not in content:
                warnings.append(
                    "No [[wikilinks]] found — every note must link to at least one other note"
                )
                valid = False

        except Exception as e:
            warnings.append(f"Error reading file: {type(e).__name__}")
            valid = False

        return {"valid": valid, "warnings": warnings}
