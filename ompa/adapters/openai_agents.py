"""
OpenAI Agents SDK adapter for OMPA.

Provides:
    OmpaAgentHooks — AgentHooks subclass that wires OMPA into an agent's lifecycle

Usage:
    pip install ompa openai-agents

    from ompa.adapters.openai_agents import OmpaAgentHooks
    from agents import Agent, Runner

    hooks = OmpaAgentHooks(vault_path="./workspace")
    agent = Agent(name="my-agent", instructions="...", hooks=hooks)
    result = await Runner.run(agent, "We decided to use Postgres")
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class OmpaAgentHooks:
    """
    OpenAI Agents SDK AgentHooks that wire OMPA into an agent's lifecycle.

    Hooks:
        on_start  → ao.session_start()  (injects vault context into agent)
        on_end    → ao.stop()           (persists session summary)
        on_tool_call_result → ao.post_tool() (syncs file writes to palace + KG)

    Also works standalone without the OpenAI Agents SDK installed
    (implements the same interface manually).

    Example:
        from ompa.adapters.openai_agents import OmpaAgentHooks
        from agents import Agent, Runner

        hooks = OmpaAgentHooks(vault_path="./workspace")
        agent = Agent(
            name="Kai",
            instructions="You are a helpful assistant with persistent memory.",
            hooks=hooks,
        )
        result = await Runner.run(agent, "What database decisions have we made?")
    """

    def __init__(
        self,
        vault_path: str | Path = ".",
        agent_name: str = "openai-agent",
        enable_semantic: bool = False,
        inject_context_in_system: bool = True,
    ):
        from ompa import Ompa

        self.vault_path = Path(vault_path)
        self.inject_context_in_system = inject_context_in_system
        self._ao = Ompa(vault_path=vault_path, agent_name=agent_name, enable_semantic=enable_semantic)
        self._session_context: str | None = None

    # ------------------------------------------------------------------
    # OpenAI Agents SDK AgentHooks interface
    # ------------------------------------------------------------------

    async def on_start(self, context: object, agent: object) -> None:
        """Called when the agent starts. Injects vault context."""
        result = self._ao.session_start()
        self._session_context = result.output

        if self.inject_context_in_system and self._session_context:
            try:
                # Append vault context to the agent's system instructions
                existing = getattr(agent, "instructions", "") or ""
                agent.instructions = existing + "\n\n---\n" + self._session_context  # type: ignore[attr-defined]
            except Exception as e:
                logger.warning("Could not inject context into agent instructions: %s", e)

    async def on_end(self, context: object, agent: object, output: object) -> None:
        """Called when the agent finishes. Persists session summary."""
        self._ao.stop()
        self._session_context = None

    async def on_tool_call_result(
        self, context: object, agent: object, tool: object, result: object
    ) -> None:
        """Called after each tool call. Syncs file writes to palace and KG."""
        try:
            tool_name = getattr(tool, "name", str(tool))
            raw_input = getattr(tool, "input", None)
            tool_input: dict[str, object] = {}
            if raw_input is not None:
                tool_input = raw_input if isinstance(raw_input, dict) else {"input": str(raw_input)}
            self._ao.post_tool(tool_name, tool_input)
        except Exception as e:
            logger.warning("OmpaAgentHooks.on_tool_call_result failed: %s", e)

    async def on_handoff(self, context: object, agent: object, source: object) -> None:
        """Called on agent handoff. Classifies the handoff event."""
        try:
            msg = f"Handoff from {getattr(source, 'name', 'unknown')} to {getattr(agent, 'name', 'unknown')}"
            self._ao.handle_message(msg)
        except Exception as e:
            logger.warning("OmpaAgentHooks.on_handoff failed: %s", e)

    # ------------------------------------------------------------------
    # Convenience: use as a context manager
    # ------------------------------------------------------------------

    def __enter__(self):
        result = self._ao.session_start()
        self._session_context = result.output
        return self

    def __exit__(self, *_):
        self._ao.stop()
        self._session_context = None

    @property
    def session_context(self) -> str:
        """The vault context string injected at session start."""
        return self._session_context or ""

    def handle_message(self, message: str) -> str:
        """Classify and route a user message. Returns routing hint."""
        result = self._ao.handle_message(message)
        return result.output
