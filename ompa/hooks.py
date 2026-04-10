"""
Lifecycle hooks for OMPA.
Handles session_start, user_message, post_tool, pre_compact, and stop events.
"""

import json
import logging
from datetime import datetime
from dataclasses import dataclass
from typing import Optional
from pathlib import Path

from .vault import Vault, Note
from .classifier import MessageClassifier
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .core import Ompa

logger = logging.getLogger(__name__)


@dataclass
class HookContext:
    """Context available during hook execution."""

    vault_path: Path
    session_id: str
    timestamp: datetime
    agent_name: str = "agent"
    memory: Optional["Ompa"] = None


@dataclass
class HookResult:
    """Result of a hook execution."""

    hook_name: str
    success: bool
    output: str = ""
    tokens_hint: int = 0
    error: Optional[str] = None


class Hook:
    """Base class for lifecycle hooks."""

    def __init__(self, name: str, token_budget: int = 0):
        self.name = name
        self.token_budget = token_budget

    def execute(self, context: HookContext, **kwargs) -> HookResult:
        raise NotImplementedError


class SessionStartHook(Hook):
    """
    Runs at session start. Loads context:
    - Vault file listing
    - North Star goals
    - Active work
    - Recent git changes
    - Open tasks
    """

    def __init__(self, token_budget: int = 2000):
        super().__init__("session_start", token_budget)
        self.classifier = MessageClassifier()

    def execute(self, context: HookContext, **kwargs) -> HookResult:
        try:
            vault = (
                context.memory.vault if context.memory else Vault(context.vault_path)
            )
            lines = []
            lines.append("## Session Context")
            lines.append(f"**Date:** {context.timestamp.strftime('%Y-%m-%d (%A)')}")
            lines.append("")

            # North Star
            north_star = vault.get_brain_note("North Star")
            if north_star:
                content = north_star.content
                lines.append("### North Star (Current Goals)")
                lines.append(
                    self._extract_section(content, "Current Focus") or content[:500]
                )
                lines.append("")

            # Recent git changes
            lines.append("### Recent Changes (last 48h)")
            try:
                import shutil
                import subprocess  # noqa: S404 — subprocess needed for git log

                git_path = shutil.which("git")
                if git_path:
                    result = subprocess.run(  # noqa: S603
                        [
                            git_path,
                            "log",
                            "--oneline",
                            "--since=48 hours ago",
                            "--no-merges",
                        ],
                        cwd=context.vault_path,
                        capture_output=True,
                        text=True,
                        timeout=5,
                    )
                    if result.returncode == 0 and result.stdout.strip():
                        for line in result.stdout.strip().split("\n")[:10]:
                            lines.append(f"- {line}")
                    else:
                        lines.append("(no recent git history)")
                else:
                    lines.append("(git not available)")
            except Exception as e:
                logger.debug("Git log failed: %s", e)
                lines.append("(git not available)")
            lines.append("")

            # Active work
            lines.append("### Active Work")
            active_notes = list(vault.config.work_folder.glob("active/*.md"))
            if active_notes:
                for note in active_notes[:5]:
                    lines.append(f"- {note.stem}")
            else:
                lines.append("(no active work notes)")
            lines.append("")

            # Vault stats
            stats = vault.get_stats()
            lines.append("### Vault Stats")
            lines.append(f"- Total notes: {stats['total_notes']}")
            lines.append(f"- Brain notes: {stats['brain_notes']}")
            lines.append(f"- Orphans (no links): {stats['orphans']}")
            lines.append("")

            # KG stats
            if context.memory and context.memory.kg:
                try:
                    kg_stats = context.memory.kg.stats()
                    if kg_stats["triple_count"] > 0:
                        lines.append("### Knowledge Graph")
                        lines.append(f"- Entities: {kg_stats['entity_count']}")
                        lines.append(f"- Current facts: {kg_stats['current_facts']}")
                        lines.append(
                            f"- Date range: {kg_stats['oldest_fact'] or 'N/A'} → {kg_stats['newest_fact'] or 'N/A'}"
                        )
                        lines.append("")
                except Exception as e:
                    logger.debug("KG stats unavailable: %s", e)

            # File listing (truncated)
            lines.append("### Vault Files")
            all_notes = vault.list_notes()
            for note in sorted(all_notes, key=lambda n: n.path)[:30]:
                try:
                    lines.append(f"- {note.path.relative_to(context.vault_path)}")
                except ValueError:
                    lines.append(f"- {note.path.name}")
            if len(all_notes) > 30:
                lines.append(f"... and {len(all_notes) - 30} more")

            output = "\n".join(lines)
            return HookResult(
                hook_name=self.name,
                success=True,
                output=output,
                tokens_hint=len(output.split()),  # Rough token estimate
            )
        except Exception as e:
            logger.error("SessionStartHook failed: %s", e, exc_info=True)
            return HookResult(hook_name=self.name, success=False, error=str(e))

    def _extract_section(self, content: str, section: str) -> str:
        """Extract a section from markdown content."""
        import re

        pattern = rf"## {section}(.*?)(?=## |$)"
        match = re.search(pattern, content, re.DOTALL | re.IGNORECASE)
        return match.group(1).strip() if match else ""


class UserMessageHook(Hook):
    """
    Runs on every user message.
    Classifies the message and injects routing hints.
    """

    def __init__(self, token_budget: int = 100):
        super().__init__("user_message", token_budget)
        self.classifier = MessageClassifier()

    def execute(self, context: HookContext, **kwargs) -> HookResult:
        message = kwargs.get("message", "")
        try:
            classification = self.classifier.classify(message)

            lines = []
            lines.append(
                f"[Classification: {classification.message_type.value.upper()} | Confidence: {classification.confidence:.0%}]"
            )
            lines.append(f"Action: {classification.suggested_action}")

            if classification.routing_hints:
                lines.append("Hints:")
                for hint in classification.routing_hints[:2]:
                    lines.append(f"  - {hint}")

            output = "\n".join(lines)
            return HookResult(
                hook_name=self.name, success=True, output=output, tokens_hint=100
            )
        except Exception as e:
            logger.error("UserMessageHook failed: %s", e, exc_info=True)
            return HookResult(hook_name=self.name, success=False, error=str(e))


class PostToolHook(Hook):
    """
    Runs after tool use (specifically .md file writes).
    Validates frontmatter, checks wikilinks.
    """

    def __init__(self, token_budget: int = 200):
        super().__init__("post_tool", token_budget)

    def execute(self, context: HookContext, **kwargs) -> HookResult:
        tool_name = kwargs.get("tool_name", "")
        tool_input = kwargs.get("tool_input", {})

        if tool_name not in ["write", "edit", "create_file"]:
            return HookResult(
                hook_name=self.name,
                success=True,
                output="(skipped - not a write operation)",
            )

        # Extract file path from tool input
        file_path = tool_input.get("file_path") or tool_input.get("path")
        if not file_path:
            return HookResult(
                hook_name=self.name, success=True, output="(skipped - no file path)"
            )

        path = Path(file_path)
        if path.suffix != ".md":
            return HookResult(
                hook_name=self.name,
                success=True,
                output="(skipped - not a markdown file)",
            )

        try:
            warnings = []
            if not path.exists():
                warnings.append("File was not created")
            else:
                note = Note.from_file(path)

                # Check frontmatter
                if not note.frontmatter.get("date"):
                    warnings.append("Missing frontmatter 'date' field")
                if not note.frontmatter.get("description"):
                    warnings.append("Missing frontmatter 'description' field")

                # Check wikilinks
                if not note.has_links():
                    warnings.append("Note has no wikilinks (orphan)")

            output = "(PostTool validation complete)"
            if warnings:
                output += "\nWarnings:\n" + "\n".join(f"  - {w}" for w in warnings)

            return HookResult(
                hook_name=self.name,
                success=len(warnings) == 0,
                output=output,
                tokens_hint=50,
            )
        except Exception as e:
            logger.error("PostToolHook failed: %s", e, exc_info=True)
            return HookResult(hook_name=self.name, success=False, error=str(e))


class PreCompactHook(Hook):
    """
    Runs before context compaction.
    Archives session transcript to thinking/session-logs/.
    """

    def __init__(self, token_budget: int = 100):
        super().__init__("pre_compact", token_budget)

    def execute(self, context: HookContext, **kwargs) -> HookResult:
        transcript = kwargs.get("transcript", "")
        try:
            session_log_path = context.vault_path / "thinking" / "session-logs"
            session_log_path.mkdir(parents=True, exist_ok=True)

            log_file = (
                session_log_path
                / f"{context.timestamp.strftime('%Y-%m-%d_%H%M%S')}_{context.session_id[:8]}.json"
            )

            log_data = {
                "session_id": context.session_id,
                "timestamp": context.timestamp.isoformat(),
                "agent": context.agent_name,
                "transcript_length": len(transcript),
                "transcript_excerpt": (
                    transcript[-2000:] if len(transcript) > 2000 else transcript
                ),
            }

            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(log_data, f, indent=2)

            return HookResult(
                hook_name=self.name,
                success=True,
                output=f"(Archived session log to {log_file.relative_to(context.vault_path)})",
                tokens_hint=50,
            )
        except Exception as e:
            logger.error("PreCompactHook failed: %s", e, exc_info=True)
            return HookResult(hook_name=self.name, success=False, error=str(e))


class StopHook(Hook):
    """
    Runs at end of session (wrap up).
    Verifies notes, updates indexes, spots uncaptured wins.
    """

    def __init__(self, token_budget: int = 500):
        super().__init__("stop", token_budget)

    def execute(self, context: HookContext, **kwargs) -> HookResult:
        try:
            vault = (
                context.memory.vault if context.memory else Vault(context.vault_path)
            )
            lines = []
            lines.append("## Wrap-Up Checklist")
            lines.append("")

            # Check for orphans
            orphans = vault.find_orphans()
            lines.append(f"**Orphan notes:** {len(orphans)}")
            if orphans:
                for orphan in orphans[:5]:
                    lines.append(f"  - {orphan.path.name}")
            lines.append("")

            # Brain notes count
            all_notes = vault.list_notes()
            brain_index = [
                n.path.relative_to(context.vault_path)
                for n in all_notes
                if "brain" in str(n.path)
            ]
            lines.append(f"**Brain notes:** {len(brain_index)}")

            # Check North Star
            north_star = vault.get_brain_note("North Star")
            if north_star:
                lines.append(f"**North Star:** {north_star.path.name}")
            else:
                lines.append("**WARNING:** No North Star found")
            lines.append("")

            # KG health
            if context.memory and context.memory.kg:
                try:
                    kg_stats = context.memory.kg.stats()
                    lines.append(
                        f"**KG:** {kg_stats['entity_count']} entities, "
                        f"{kg_stats['current_facts']} current facts"
                    )
                    if kg_stats["triple_count"] == 0:
                        lines.append(
                            "**WARNING:** KG is empty — run `ao kg-populate` or `ao sync`"
                        )
                except Exception as e:
                    logger.debug("KG stats unavailable in wrap-up: %s", e)

            output = "\n".join(lines)
            return HookResult(
                hook_name=self.name, success=True, output=output, tokens_hint=200
            )
        except Exception as e:
            logger.error("StopHook failed: %s", e, exc_info=True)
            return HookResult(hook_name=self.name, success=False, error=str(e))


class HookManager:
    """Manages and executes lifecycle hooks."""

    def __init__(self, vault_path: str | Path, agent_name: str = "agent"):
        self.vault_path = Path(vault_path)
        self.agent_name = agent_name
        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.timestamp = datetime.now()

        # Register default hooks
        self.hooks = {
            "session_start": SessionStartHook(),
            "user_message": UserMessageHook(),
            "post_tool": PostToolHook(),
            "pre_compact": PreCompactHook(),
            "stop": StopHook(),
        }

    def _create_context(self, memory=None) -> HookContext:
        return HookContext(
            vault_path=self.vault_path,
            session_id=self.session_id,
            timestamp=self.timestamp,
            agent_name=self.agent_name,
            memory=memory,
        )

    def run_session_start(self, memory=None) -> HookResult:
        """Run session start hook."""
        context = self._create_context(memory)
        return self.hooks["session_start"].execute(context)

    def run_user_message(self, message: str, memory=None) -> HookResult:
        """Run user message hook."""
        context = self._create_context(memory)
        return self.hooks["user_message"].execute(context, message=message)

    def run_post_tool(
        self, tool_name: str, tool_input: dict, memory=None
    ) -> HookResult:
        """Run post tool hook."""
        context = self._create_context(memory)
        return self.hooks["post_tool"].execute(
            context, tool_name=tool_name, tool_input=tool_input
        )

    def run_pre_compact(self, transcript: str, memory=None) -> HookResult:
        """Run pre-compact hook."""
        context = self._create_context(memory)
        return self.hooks["pre_compact"].execute(context, transcript=transcript)

    def run_stop(self, memory=None) -> HookResult:
        """Run stop hook."""
        context = self._create_context(memory)
        return self.hooks["stop"].execute(context)

    def register_hook(self, name: str, hook: Hook) -> None:
        """Register a custom hook."""
        self.hooks[name] = hook
