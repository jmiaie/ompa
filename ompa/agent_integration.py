"""
Agent integration layer for OMPA.

Provides Session — a wrapper around Ompa that handles cold-start edge cases
where the memory store is not pre-initialized before session_start() is called.
This is common on constrained nodes (2 vCPU / 4 GB RAM) or containerized
environments (e.g. no-TUN containers) where eager initialization can fail.

Usage:
    from ompa.agent_integration import Session

    session = Session(vault_path="./workspace")
    session.session_start()       # safe on cold start: memory initialises lazily
    session.handle_message("...")
    session.stop()

    # Or as a context manager:
    with Session(vault_path="./workspace") as s:
        s.handle_message("We decided to use Postgres")
"""

from __future__ import annotations

import logging
from pathlib import Path

from .hooks import HookResult

logger = logging.getLogger(__name__)


class Session:
    """
    Cold-start-safe wrapper around Ompa with lazy memory initialization.

    The ``memory`` property constructs the underlying Ompa instance on first
    access, so Session() itself never raises even in constrained environments
    where sentence-transformers or SQLite may be slow to initialize.

    Calling session_start() without a prior session_init() is fully supported —
    memory is initialized automatically on first access.
    """

    def __init__(
        self,
        vault_path: str | Path = ".",
        agent_name: str = "agent",
        enable_semantic: bool = True,
        **kwargs,
    ):
        self._vault_path = Path(vault_path)
        self._agent_name = agent_name
        self._enable_semantic = enable_semantic
        self._kwargs = kwargs
        self._memory: object | None = None

    @property
    def memory(self):
        """Return the Ompa instance, constructing it on first access."""
        if self._memory is None:
            self._memory = _make_ompa(
                self._vault_path,
                self._agent_name,
                self._enable_semantic,
                **self._kwargs,
            )
        return self._memory

    def session_init(self) -> None:
        """Explicitly initialize memory (optional; memory is lazy by default)."""
        _ = self.memory

    def session_start(self) -> HookResult:
        """Run the session-start lifecycle hook."""
        return self.memory.session_start()

    def handle_message(self, message: str) -> HookResult:
        """Classify a user message and return routing hints."""
        return self.memory.handle_message(message)

    def post_tool(self, tool_name: str, tool_input: dict) -> HookResult:
        """Run the post-tool hook after a tool call."""
        return self.memory.post_tool(tool_name, tool_input)

    def pre_compact(self, transcript: str) -> HookResult:
        """Run the pre-compact hook before context compaction."""
        return self.memory.pre_compact(transcript)

    def stop(self) -> HookResult:
        """Run the stop/wrap-up lifecycle hook."""
        return self.memory.stop()

    def __enter__(self) -> Session:
        self.session_start()
        return self

    def __exit__(self, *_) -> None:
        self.stop()


def _make_ompa(
    vault_path: Path,
    agent_name: str,
    enable_semantic: bool,
    **kwargs,
):
    """Construct an Ompa instance; isolated so tests can patch it easily."""
    from .core import Ompa

    return Ompa(
        vault_path=vault_path,
        agent_name=agent_name,
        enable_semantic=enable_semantic,
        **kwargs,
    )
