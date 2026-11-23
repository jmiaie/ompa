"""
Vault management for OMPA.
Handles note organization, templates, wikilinks, and frontmatter validation.
"""

import heapq
import logging
import os
import re
import shutil
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import frontmatter
from frontmatter.default_handlers import YAMLHandler

logger = logging.getLogger(__name__)

# Shared exclude patterns for vault traversal
DEFAULT_EXCLUDE_PATTERNS = [".git", ".claude", "thinking"]


def is_excluded(path: Path | str, root: Path | str, patterns: list[str] | None = None) -> bool:
    """True if *path* lies in an excluded folder of the vault at *root*.

    A pattern names a folder (``"thinking"``) or a folder path
    (``"work/archive"``) and matches whole path components below the vault
    root.  Matching used to be a substring test on the absolute path, so a
    vault kept under ``~/.claude/`` or ``design-thinking/`` excluded every
    note in it, and a note called ``Rethinking auth.md`` was skipped.
    """
    patterns = DEFAULT_EXCLUDE_PATTERNS if patterns is None else patterns
    path = Path(path)
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        # Not under root as spelled (e.g. one side is relative): compare resolved.
        try:
            parts = path.resolve().relative_to(Path(root).resolve()).parts
        except (ValueError, OSError):
            parts = path.parts
    for pattern in patterns:
        want = tuple(p for p in str(pattern).replace("\\", "/").split("/") if p)
        n = len(want)
        if n and any(parts[i : i + n] == want for i in range(len(parts) - n + 1)):
            return True
    return False


def _exclusion_sets(patterns: list[str] | None) -> tuple[frozenset[str], list[tuple[str, ...]]]:
    """Split exclude patterns into folder names and multi-component folder paths."""
    patterns = DEFAULT_EXCLUDE_PATTERNS if patterns is None else patterns
    names: set[str] = set()
    paths: list[tuple[str, ...]] = []
    for pattern in patterns:
        want = tuple(p for p in str(pattern).replace("\\", "/").split("/") if p)
        if len(want) == 1:
            names.add(want[0])
        elif want:
            paths.append(want)
    return frozenset(names), paths


# ``*.md`` as pathlib's glob matches it: case-sensitive except on Windows.
if os.path.normcase("A") == "A":

    def _is_note_name(name: str) -> bool:
        return name.endswith(".md")
else:  # pragma: no cover - Windows

    def _is_note_name(name: str) -> bool:
        return name.lower().endswith(".md")


def walk_notes(
    root: Path | str, patterns: list[str] | None = None
) -> Iterator[tuple[str, os.DirEntry]]:
    """Every note below *root* that :func:`is_excluded` would keep, in one walk.

    Yields ``(path, entry)``: the path spelled as ``str(Path(root) / relative)``
    and the directory entry, whose ``stat()`` is cheaper than a fresh one.

    This returns what ``Path(root).rglob("*.md")`` filtered through
    :func:`is_excluded` returns, in the same order (a folder's notes, then its
    subfolders).  It is about three times faster, because an excluded folder
    is never entered and no ``Path`` object is built per file.  Like ``rglob``,
    it does not follow symlinks to directories.
    """
    names, paths = _exclusion_sets(patterns)
    base = str(Path(root))
    prefix = "" if base == "." else (base if base.endswith(os.sep) else base + os.sep)
    stack: list[tuple[str, tuple[str, ...]]] = [("", ())]
    while stack:
        rel, parts = stack.pop()
        try:
            with os.scandir(prefix + rel or ".") as it:
                entries = list(it)
        except OSError:
            continue
        subdirs: list[str] = []
        for entry in entries:
            name = entry.name
            if name in names:
                continue
            if paths:
                here = parts + (name,)
                if any(here[-len(want) :] == want for want in paths):
                    continue
            if _is_note_name(name):
                yield prefix + rel + name, entry
            try:
                if entry.is_dir() and not entry.is_symlink():
                    subdirs.append(name)
            except OSError:
                continue
        for name in reversed(subdirs):
            stack.append((rel + name + os.sep, parts + (name,)))


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
    brain_folder: Path = None
    work_folder: Path = None
    org_folder: Path = None
    perf_folder: Path = None
    thinking_folder: Path = None
    templates_folder: Path = None

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

        try:
            post = frontmatter.load(path)
            content = post.content.strip()
            return cls(
                path=path,
                frontmatter=dict(post.metadata),
                content=content,
                links=cls._extract_wikilinks(content),
            )
        except Exception as e:
            # Fallback: read raw content if frontmatter parsing fails
            logger.debug("Frontmatter parse failed for %s: %s", path, e)
            try:
                text = path.read_text(encoding="utf-8")
                return cls(path=path, content=text, links=cls._extract_wikilinks(text))
            except Exception as e:
                logger.debug("Could not read %s: %s", path, e)
                return cls(path=path)

    @staticmethod
    def _extract_wikilinks(text: str) -> list[str]:
        """Extract [[wikilinks]] from text, normalizing targets."""
        raw = re.findall(r"\[\[([^\]]+)\]\]", text)
        normalized = []
        for link in raw:
            # Strip display text: [[target|display]] → target
            target = link.split("|")[0].strip()
            # Strip .md extension if present (will be re-added during resolution)
            if target.lower().endswith(".md"):
                target = target[:-3]
            if target:
                normalized.append(target)
        return normalized

    def has_links(self) -> bool:
        """Check if note has any wikilinks."""
        return len(self.links) > 0

    def save(self) -> None:
        """Save note to file with frontmatter.

        The text goes to a temporary file in the same folder and is then moved
        into place, so a crash or a full disk mid-write leaves the previous
        version of the note rather than a truncated one.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        post = frontmatter.Post(self.content, **self.frontmatter)
        # write through a symlink instead of replacing the link itself
        target = self.path.resolve() if self.path.is_symlink() else self.path
        tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(frontmatter.dumps(post))
            if target.exists():
                shutil.copymode(target, tmp)
            os.replace(tmp, target)
        finally:
            tmp.unlink(missing_ok=True)


class Vault:
    """Manages the OMPA vault structure."""

    # Folder structure
    STRUCTURE = {
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

    def _ensure_structure(self) -> None:
        """Create vault folder structure if it doesn't exist."""
        for folder in self.STRUCTURE:
            folder_path = self.vault_path / folder
            folder_path.mkdir(parents=True, exist_ok=True)

    def note_paths(self, exclude_patterns: list[str] | None = None) -> list[Path]:
        """Paths of all markdown notes in the vault, without reading any of them."""
        return [Path(p) for p, _ in walk_notes(self.vault_path, exclude_patterns or None)]

    def list_notes(self, exclude_patterns: list[str] = None) -> list[Note]:
        """List all markdown notes in the vault."""
        return [Note.from_file(path) for path in self.note_paths(exclude_patterns)]

    @staticmethod
    def _links_and_wing(path: Path) -> tuple[list[str], str]:
        """A note's wikilinks and ``wing``, as :meth:`Note.from_file` reports them.

        Vault statistics need only these two things from every note, and parsing
        each note's YAML took most of the time.  The YAML is only parsed here when
        it can change the answer: when the frontmatter mentions ``wing``, or holds
        a ``[[`` (which :meth:`Note.from_file` counts as a link if the YAML turns
        out to be malformed).  Everything else is decided from the text.
        """
        try:
            text = path.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return [], ""
        if text.startswith(("{", "+++")):  # JSON or TOML frontmatter: rare, take no chances
            note = Note.from_file(path)
            return note.links, str(note.frontmatter.get("wing", ""))
        body = text
        if YAMLHandler.FM_BOUNDARY.match(text):
            pieces = YAMLHandler.FM_BOUNDARY.split(text, 2)
            if len(pieces) == 3:
                if "wing" in pieces[1] or "[[" in pieces[1]:
                    note = Note.from_file(path)
                    return note.links, str(note.frontmatter.get("wing", ""))
                body = pieces[2]
        return Note._extract_wikilinks(body.strip()), ""

    def _link_view(self) -> list[tuple[Path, list[str], str]]:
        """``(path, wikilinks, wing)`` for every note, one read per note."""
        return [(path, *self._links_and_wing(path)) for path in self.note_paths()]

    def _linked_paths(self, view: list[tuple[Path, list[str], str]]) -> set[Path]:
        """Every note that some note's wikilink resolves to."""
        filename_index = self._build_filename_index([path for path, _, _ in view])
        linked: set[Path] = set()
        for _, links, _ in view:
            for link in links:
                resolved = self._resolve_wikilink(link, filename_index)
                if resolved:
                    linked.add(resolved)
        return linked

    def _build_filename_index(self, notes: "list[Note] | list[Path]") -> dict[str, Path]:
        """Build a case-insensitive filename → path index for wikilink resolution."""
        index = {}
        for note in notes:
            path = note.path if isinstance(note, Note) else note
            # Index by stem (without .md) — case-insensitive
            key = path.stem.lower()
            index[key] = path
            # Also index by full filename
            index[path.name.lower()] = path
        return index

    def _resolve_wikilink(self, link: str, filename_index: dict[str, Path]) -> Path | None:
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

    def orphan_paths(self) -> list[Path]:
        """Paths of notes with no incoming links from other notes.

        Use this when the paths are all that is needed: :meth:`find_orphans`
        also loads every orphan's frontmatter and content.
        """
        view = self._link_view()
        linked = self._linked_paths(view)
        return [
            path
            for path, _, _ in view
            if path not in linked and path.name not in ["Home.md", "README.md"]
        ]

    def find_orphans(self) -> list[Note]:
        """Find notes with no incoming links from other notes."""
        return [Note.from_file(path) for path in self.orphan_paths()]

    def first_note_paths(self, n: int) -> tuple[list[Path], int]:
        """The first *n* note paths in sorted order, and how many notes there are."""
        paths = self.note_paths()
        return heapq.nsmallest(n, paths), len(paths)

    def search_by_name(self, query: str) -> list[Note]:
        """Search notes by filename."""
        query_lower = query.lower()
        return [
            Note.from_file(path) for path in self.note_paths() if query_lower in path.stem.lower()
        ]

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
        except ValueError:
            raise ValueError(f"Invalid brain note name: {name!r}") from None
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
        except ValueError:
            raise ValueError(f"Invalid brain note name: {name!r}") from None
        path.parent.mkdir(parents=True, exist_ok=True)

        if append and path.exists():
            note = Note.from_file(path)
            note.content += f"\n{content}"
        else:
            note = Note(path=path, content=content)

        note.save()

    def create_from_template(self, template_name: str, target_name: str, **kwargs) -> Note:
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
        return note

    def get_stats(self) -> dict:
        """Get vault statistics."""
        # One read per note, and no YAML parse unless it can change the numbers.
        notes = self._link_view()
        linked_files = self._linked_paths(notes)

        orphan_count = sum(
            1
            for path, _, _ in notes
            if path not in linked_files and path.name not in ["Home.md", "README.md"]
        )

        folder_counts: dict[str, int] = {}
        brain_count = 0
        for path, _, wing in notes:
            folder = path.parent.name or "root"
            folder_counts[folder] = folder_counts.get(folder, 0) + 1

            # Count brain notes: in brain/ folder OR wing=brain in frontmatter
            if "brain" in path.parts or wing.lower() == "brain":
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
        """
        Validate a markdown file for frontmatter and wikilinks.
        File must be within the vault directory.
        Returns {valid: bool, warnings: list[str]}.
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

        if "templates" in path.parts or "thinking" in path.parts or ".claude" in path.parts:
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
                        warnings.append("Missing 'description' in frontmatter (~150 chars)")
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
