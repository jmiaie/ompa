"""
LangChain adapters for OMPA.

Provides:
    OmpaMemory     — BaseChatMemory-compatible memory backed by an OMPA vault
    OmpaRetriever  — BaseRetriever wrapping ao.search() for use in RAG chains

Usage:
    pip install ompa langchain-core

    from ompa.adapters.langchain import OmpaMemory, OmpaRetriever

    memory = OmpaMemory(vault_path="./workspace")
    retriever = OmpaRetriever(vault_path="./workspace")
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _require_langchain():
    try:
        import langchain_core  # noqa: F401

        return True
    except ImportError:
        raise ImportError(
            "LangChain is not installed. Install with: pip install ompa[langchain]"
        ) from None


class OmpaMemory:
    """
    LangChain-compatible memory component backed by an OMPA vault.

    Implements the BaseChatMemory interface:
        load_memory_variables(inputs) → dict
        save_context(inputs, outputs) → None
        clear() → None

    Compatible with LangChain 0.1+ (both old and new style chains).
    Works standalone without LangChain installed.

    Example:
        from ompa.adapters.langchain import OmpaMemory
        from langchain.chains import ConversationChain
        from langchain_anthropic import ChatAnthropic

        memory = OmpaMemory(vault_path="./workspace")
        chain = ConversationChain(
            llm=ChatAnthropic(model="claude-sonnet-4-6"),
            memory=memory,
        )
        chain.predict(input="We decided to use Postgres")
    """

    memory_key: str = "history"
    human_prefix: str = "Human"
    ai_prefix: str = "AI"
    return_messages: bool = False
    input_key: str = "input"
    output_key: str = "output"

    def __init__(
        self,
        vault_path: str | Path = ".",
        agent_name: str = "langchain-agent",
        enable_semantic: bool = False,
        memory_key: str = "history",
        return_messages: bool = False,
    ):
        from ompa import Ompa

        self.vault_path = Path(vault_path)
        self.memory_key = memory_key
        self.return_messages = return_messages
        self._ao = Ompa(
            vault_path=vault_path, agent_name=agent_name, enable_semantic=enable_semantic
        )
        self._session_context: str = ""
        self._started = False

    def _ensure_started(self) -> None:
        if not self._started:
            result = self._ao.session_start()
            self._session_context = result.output
            self._started = True

    @property
    def memory_variables(self) -> list[str]:
        return [self.memory_key]

    def load_memory_variables(self, inputs: dict[str, Any]) -> dict[str, Any]:
        """Return vault context as the memory variable injected into the chain."""
        self._ensure_started()

        human_input = inputs.get(self.input_key, "")
        if human_input:
            hint = self._ao.handle_message(human_input)
            context = self._session_context
            if hint.output:
                context += f"\n\n[Memory hint: {hint.output}]"
        else:
            context = self._session_context

        if self.return_messages:
            try:
                from langchain_core.messages import SystemMessage

                return {self.memory_key: [SystemMessage(content=context)]}
            except ImportError:
                pass

        return {self.memory_key: context}

    def save_context(self, inputs: dict[str, Any], outputs: dict[str, Any]) -> None:
        """Classify and persist each exchange to the vault."""
        human_input = inputs.get(self.input_key, "")
        ai_output = outputs.get(self.output_key, outputs.get("response", ""))

        if not human_input:
            return

        c = self._ao.classify(human_input)
        self._ao.write(
            content=f"{self.human_prefix}: {human_input}\n\n{self.ai_prefix}: {ai_output}",
            tags=[c.message_type.value.lower(), "langchain"],
        )

    def clear(self) -> None:
        """Run session stop hook and reset state."""
        if self._started:
            self._ao.stop()
        self._started = False
        self._session_context = ""

    # LangChain duck-typing compatibility
    def dict(self, **kwargs) -> dict:
        return {"memory_key": self.memory_key, "vault_path": str(self.vault_path)}


class OmpaRetriever:
    """
    LangChain BaseRetriever-compatible retriever backed by OMPA's semantic search.

    Example:
        from ompa.adapters.langchain import OmpaRetriever
        from langchain.chains import RetrievalQA
        from langchain_anthropic import ChatAnthropic

        retriever = OmpaRetriever(vault_path="./workspace", k=5)
        qa = RetrievalQA.from_chain_type(
            llm=ChatAnthropic(model="claude-sonnet-4-6"),
            retriever=retriever,
        )
        qa.run("What authentication decisions have we made?")
    """

    def __init__(
        self,
        vault_path: str | Path = ".",
        k: int = 5,
        agent_name: str = "langchain-retriever",
    ):
        from ompa import Ompa

        self._ao = Ompa(vault_path=vault_path, agent_name=agent_name, enable_semantic=True)
        self.k = k

    def get_relevant_documents(self, query: str) -> list:
        """Return LangChain Documents for the top-k results."""
        results = self._ao.search(query, limit=self.k)
        try:
            from langchain_core.documents import Document

            return [
                Document(
                    page_content=r.content_excerpt,
                    metadata={"source": r.path, "score": r.score, "match_type": r.match_type},
                )
                for r in results
            ]
        except ImportError:
            # Return dicts if langchain_core not installed
            return [
                {
                    "page_content": r.content_excerpt,
                    "metadata": {"source": r.path, "score": r.score},
                }
                for r in results
            ]

    async def aget_relevant_documents(self, query: str) -> list:
        """Async variant — delegates to sync (OMPA search is synchronous)."""
        return self.get_relevant_documents(query)

    # BaseRetriever duck-typing
    def invoke(self, input: str, config: Any = None, **kwargs) -> list:
        return self.get_relevant_documents(input)
