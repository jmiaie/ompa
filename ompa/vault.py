"""
Vault management for OMPA.
Handles note organization, templates, wikilinks, and frontmatter validation.
"""

import logging
import re
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional
import frontmatter

logger = logging.getLogger(__name__)

# Shared exclude patterns for vault traversal
DEFAULT_EXCLUDE_PATTERNS = [".git", ".claude", "thinking"]


def _safe_resolve(base: Path, untrusted: str) -> Path:
    """
    Resolve an untrusted path relative to a base directory.
    Raises ValueError if the resolved path escapes the base.
    """
    resolved = (base / untrusted).resolve()
    base_resolved = base.resolve()
    if not str(resolved).startswith(str(base_resolved)):
        raise ValueError(f"Path traversal blocked: {untrusted!r} escapes {base}")
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
        """Extract [[wikilinks]] from text."""
        return re.findall(r"\[\[([^\]]+)\]\]", text)

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

    def list_notes(self, exclude_patterns: list[str] = None) -> list[Note]:
        """List all markdown notes in the vault."""
        exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS
        notes = []

        for path in self.vault_path.rglob("*.md"):
            # Check exclusions
            if any(excl in str(path) for excl in exclude_patterns):
                continue
            notes.append(Note.from_file(path))

        return notes

    def find_orphans(self) -> list[Note]:
        """Find notes with no links to other notes."""
        all_notes = self.list_notes()
        linked_files = set()

        for note in all_notes:
            for link in note.links:
                # Resolve wikilink to file
                linked_path = self.vault_path / link
                if not linked_path.exists():
                    linked_path = self.vault_path / f"{link}.md"
                if linked_path.exists():
                    linked_files.add(linked_path)

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

    def get_brain_note(self, name: str) -> Optional[Note]:
        """Get a brain note by name. Name is sanitized to prevent path traversal."""
        safe_name = Path(name).name  # Strip any directory components
        path = self.config.brain_folder / f"{safe_name}.md"
        path = path.resolve()
        # Ensure we stay within brain folder
        if not str(path).startswith(str(self.config.brain_folder.resolve())):
            raise ValueError(f"Invalid brain note name: {name!r}")
        if path.exists():
            return Note.from_file(path)
        return None

    def update_brain_note(self, name: str, content: str, append: bool = False) -> None:
        """Update a brain note. Name is sanitized to prevent path traversal."""
        safe_name = Path(name).name  # Strip any directory components
        path = self.config.brain_folder / f"{safe_name}.md"
        path = path.resolve()
        if not str(path).startswith(str(self.config.brain_folder.resolve())):
            raise ValueError(f"Invalid brain note name: {name!r}")
        path.parent.mkdir(parents=True, exist_ok=True)

        if append and path.exists():
            note = Note.from_file(path)
            note.content += f"\n{content}"
        else:
            note = Note(path=path, content=content)

        note.save()

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
        return note

    def get_stats(self) -> dict:
        """Get vault statistics."""
        notes = self.list_notes()

        # Build linked set in one pass (instead of calling find_orphans which re-reads)
        linked_files = set()
        for note in notes:
            for link in note.links:
                linked_path = self.vault_path / link
                if not linked_path.exists():
                    linked_path = self.vault_path / f"{link}.md"
                if linked_path.exists():
                    linked_files.add(linked_path)

        orphan_count = sum(
            1
            for n in notes
            if n.path not in linked_files
            and n.path.name not in ["Home.md", "README.md"]
        )

        folder_counts = {}
        for note in notes:
            folder = note.path.parent.name or "root"
            folder_counts[folder] = folder_counts.get(folder, 0) + 1

        return {
            "total_notes": len(notes),
            "orphans": orphan_count,
            "folder_counts": folder_counts,
            "brain_notes": (
                len(list(self.config.brain_folder.glob("*.md")))
                if self.config.brain_folder.exists()
                else 0
            ),
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
            if not str(path).startswith(str(self.vault_path)):
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
