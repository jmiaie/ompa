"""
LlamaIndex adapters for OMPA.

Provides:
    OmpaReader          — BaseReader that loads vault notes as LlamaIndex Documents
    OmpaVaultRetriever  — BaseRetriever wrapping ao.search()

Usage:
    pip install ompa llama-index-core

    from ompa.adapters.llamaindex import OmpaReader, OmpaVaultRetriever

    # Load all vault notes as documents
    reader = OmpaReader(vault_path="./workspace")
    documents = reader.load_data()

    # Use as a retriever in a query engine
    retriever = OmpaVaultRetriever(vault_path="./workspace", similarity_top_k=5)
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class OmpaReader:
    """
    LlamaIndex SimpleDirectoryReader-compatible reader for OMPA vaults.

    Loads vault notes as LlamaIndex Document objects, preserving frontmatter
    as metadata. Integrates with any LlamaIndex pipeline that accepts documents.

    Works standalone (returns dicts) if llama-index-core is not installed.

    Example:
        from ompa.adapters.llamaindex import OmpaReader
        from llama_index.core import VectorStoreIndex

        reader = OmpaReader(vault_path="./workspace")
        documents = reader.load_data()
        index = VectorStoreIndex.from_documents(documents)
        engine = index.as_query_engine()
        response = engine.query("What database decisions have we made?")
    """

    def __init__(
        self,
        vault_path: str | Path = ".",
        include_brain: bool = True,
        include_work: bool = True,
        include_org: bool = True,
        include_perf: bool = True,
        tags_filter: list[str] | None = None,
    ):
        from ompa import Vault

        self.vault_path = Path(vault_path)
        self._vault = Vault(vault_path)
        self._include_folders = set()
        if include_brain:
            self._include_folders.add("brain")
        if include_work:
            self._include_folders.add("work")
        if include_org:
            self._include_folders.add("org")
        if include_perf:
            self._include_folders.add("perf")
        self._tags_filter = set(tags_filter) if tags_filter else None

    def load_data(self, **kwargs) -> list:
        """
        Load vault notes as LlamaIndex Documents.

        Returns LlamaIndex Document objects if llama-index-core is installed,
        otherwise returns dicts with the same structure.
        """
        notes = self._vault.list_notes()
        documents = []

        for note in notes:
            # Folder filter
            parts = set(note.path.parts)
            if self._include_folders and not (parts & self._include_folders):
                continue

            # Tags filter
            if self._tags_filter:
                raw_tags = note.frontmatter.get("tags", [])
                note_tags: set[str] = set(raw_tags) if isinstance(raw_tags, list) else set()
                if not (note_tags & self._tags_filter):
                    continue

            try:
                rel_path = note.path.relative_to(self.vault_path)
            except ValueError:
                rel_path = note.path

            metadata = {
                "source": str(rel_path),
                "title": note.path.stem,
                "date": note.frontmatter.get("date", ""),
                "tags": note.frontmatter.get("tags", []),
                "vault_path": str(self.vault_path),
            }

            try:
                from llama_index.core import Document
                documents.append(
                    Document(
                        text=note.content,
                        metadata=metadata,
                        id_=str(rel_path),
                    )
                )
            except ImportError:
                documents.append({"text": note.content, "metadata": metadata})

        logger.info("OmpaReader loaded %d documents from %s", len(documents), self.vault_path)
        return documents


class OmpaVaultRetriever:
    """
    LlamaIndex BaseRetriever-compatible retriever backed by OMPA's semantic search.

    Example:
        from ompa.adapters.llamaindex import OmpaVaultRetriever
        from llama_index.core.query_engine import RetrieverQueryEngine
        from llama_index.llms.anthropic import Anthropic

        retriever = OmpaVaultRetriever(vault_path="./workspace", similarity_top_k=5)
        engine = RetrieverQueryEngine.from_args(retriever=retriever)
        response = engine.query("What authentication decisions have we made?")
    """

    def __init__(
        self,
        vault_path: str | Path = ".",
        similarity_top_k: int = 5,
        agent_name: str = "llamaindex-retriever",
    ):
        from ompa import Ompa

        self._ao = Ompa(vault_path=vault_path, agent_name=agent_name, enable_semantic=True)
        self.similarity_top_k = similarity_top_k

    def _retrieve(self, query: str) -> list:
        """Return NodeWithScore objects (or dicts if llama_index not installed)."""
        results = self._ao.search(query, limit=self.similarity_top_k)

        try:
            from llama_index.core.schema import NodeWithScore, TextNode
            return [
                NodeWithScore(
                    node=TextNode(
                        text=r.content_excerpt,
                        metadata={"source": r.path, "match_type": r.match_type},
                    ),
                    score=r.score,
                )
                for r in results
            ]
        except ImportError:
            return [
                {"text": r.content_excerpt, "score": r.score, "metadata": {"source": r.path}}
                for r in results
            ]

    def retrieve(self, query: Any) -> list:
        """LlamaIndex BaseRetriever interface."""
        if hasattr(query, "query_str"):
            query = query.query_str
        return self._retrieve(str(query))

    async def aretrieve(self, query: Any) -> list:
        return self.retrieve(query)
