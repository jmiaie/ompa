"""
OMPA + LangChain — drop-in memory component.

Wraps Ompa as a LangChain BaseMemory so any LangChain chain or agent
gets persistent vault memory with zero changes to the rest of the chain.

Usage:
    pip install langchain ompa

    from examples.langchain_agent.ompa_memory import OmpaMemory
    from langchain.chains import ConversationChain
    from langchain_anthropic import ChatAnthropic

    memory = OmpaMemory(vault_path="./workspace")
    chain = ConversationChain(llm=ChatAnthropic(model="claude-sonnet-4-6"), memory=memory)
    chain.predict(input="We decided to use Postgres for the main database")
"""

from pathlib import Path
from typing import Any

from ompa import Ompa

try:
    from langchain.memory.chat_memory import BaseChatMemory
    from langchain.schema import HumanMessage
    LANGCHAIN_AVAILABLE = True
except ImportError:
    LANGCHAIN_AVAILABLE = False
    BaseChatMemory = object


class OmpaMemory(BaseChatMemory if LANGCHAIN_AVAILABLE else object):
    """
    LangChain-compatible memory backed by an OMPA vault.

    Persists every conversation turn to the vault, classified by message type.
    Injects relevant vault context at each session start.
    """

    vault_path: str = "./workspace"
    memory_key: str = "history"
    human_prefix: str = "Human"
    ai_prefix: str = "AI"
    return_messages: bool = False

    def __init__(self, vault_path: str | Path = "./workspace", **kwargs):
        super().__init__(**kwargs)
        self.vault_path = str(vault_path)
        self._ao = Ompa(vault_path=vault_path, enable_semantic=False)
        self._session_context = self._ao.session_start().output

    @property
    def memory_variables(self) -> list[str]:
        return [self.memory_key]

    def load_memory_variables(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Return vault context as memory variable."""
        message = inputs.get("input", "")
        if message:
            hint = self._ao.handle_message(message)
            context = f"{self._session_context}\n\n[Routing hint: {hint.output}]"
        else:
            context = self._session_context

        if self.return_messages:
            return {self.memory_key: [HumanMessage(content=context)]}
        return {self.memory_key: context}

    def save_context(self, inputs: dict[str, Any], outputs: dict[str, Any]) -> None:
        """Classify and persist each exchange to the vault."""
        human_input = inputs.get("input", "")
        ai_output = outputs.get("response", outputs.get("output", ""))

        if human_input:
            c = self._ao.classify(human_input)
            self._ao.write(
                content=f"Human: {human_input}\n\nAI: {ai_output}",
                tags=[c.message_type.value.lower()],
            )

    def clear(self) -> None:
        """Run session stop hook."""
        self._ao.stop()


# ---------------------------------------------------------------------------
# Demo — run directly to see it work without LangChain
# ---------------------------------------------------------------------------

def demo_standalone():
    """Demonstrate OMPA memory without LangChain dependency."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        ao = Ompa(vault_path=tmp, enable_semantic=False)

        # Session start
        ctx = ao.session_start()
        print("=== Session Context (first 200 chars) ===")
        print(ctx.output[:200])
        print()

        # Simulate conversation turns
        messages = [
            "We decided to use Postgres over MySQL for the main database",
            "The auth service went down for 15 minutes — memory leak in the JWT handler",
            "We shipped the bulk export feature ahead of schedule",
        ]

        for msg in messages:
            hint = ao.handle_message(msg)
            c = ao.classify(msg)
            print(f"Message: {msg[:60]}...")
            print(f"  → {c.message_type.value} ({c.confidence:.0%})")
            print()

        # Wrap up
        ao.stop()
        print("Session ended. Vault preserved at:", tmp)


if __name__ == "__main__":
    demo_standalone()
