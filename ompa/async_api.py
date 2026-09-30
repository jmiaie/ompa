"""
AsyncOmpa — async-native wrapper around Ompa.

Runs all blocking operations in a thread-pool executor so they don't block
the event loop in async agent frameworks (OpenAI Agents SDK, asyncio-based
swarms, FastAPI endpoints, etc.).

Usage:
    from ompa.async_api import AsyncOmpa

    async def run():
        async with AsyncOmpa(vault_path="./workspace") as ao:
            context = await ao.session_start()
            hint = await ao.handle_message("We decided to use Postgres")
            results = await ao.search("database decisions")

    # Or manually:
    ao = AsyncOmpa(vault_path="./workspace")
    context = await ao.session_start()
    await ao.stop()

Install notes:
    Core OMPA (sync) has no new deps.
    For aiosqlite-backed KG (optional, true async writes):
        pip install aiosqlite
"""

from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class AsyncOmpa:
    """
    Async-native OMPA client.

    All methods are coroutines that delegate to the synchronous `Ompa` class
    via `asyncio.get_event_loop().run_in_executor()`. This keeps the event loop
    unblocked during I/O-heavy operations (vault reads, SQLite queries, semantic
    search model inference).

    The internal thread pool is per-instance and shut down on `close()`.

    Example:
        from ompa.async_api import AsyncOmpa

        async def agent_loop(messages):
            async with AsyncOmpa(vault_path="./workspace") as ao:
                for msg in messages:
                    hint = await ao.handle_message(msg)
                    print(hint.output)

    Multi-agent usage (shared vault, concurrent agents):
        import asyncio
        from ompa.async_api import AsyncOmpa

        async def run_agent(name, messages):
            ao = AsyncOmpa(vault_path="./shared-vault", agent_name=name)
            await ao.session_start()
            for msg in messages:
                await ao.handle_message(msg)
            await ao.stop()

        await asyncio.gather(
            run_agent("Aria", [...]),
            run_agent("Bex", [...]),
            run_agent("Coda", [...]),
        )
    """

    def __init__(
        self,
        vault_path: str | Path = ".",
        agent_name: str = "async-agent",
        enable_semantic: bool = False,
        embedding_backend=None,
        shared_vault_path: str | Path = None,
        personal_vault_path: str | Path = None,
        isolation_mode: str = "strict",
        max_workers: int = 4,
    ):
        from .core import Ompa

        self._ompa = Ompa(
            vault_path=vault_path,
            agent_name=agent_name,
            enable_semantic=enable_semantic,
            embedding_backend=embedding_backend,
            shared_vault_path=shared_vault_path,
            personal_vault_path=personal_vault_path,
            isolation_mode=isolation_mode,
        )
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=f"ompa-{agent_name}",
        )

    async def _run(self, fn, *args, **kwargs) -> Any:
        """Run a sync function in the executor without blocking the event loop."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(self._executor, partial(fn, *args, **kwargs))

    # ------------------------------------------------------------------
    # Lifecycle hooks
    # ------------------------------------------------------------------

    async def session_start(self):
        """Async session start — injects vault context (~2K tokens)."""
        return await self._run(self._ompa.session_start)

    async def handle_message(self, message: str):
        """Async user message hook — classifies and returns routing hint."""
        return await self._run(self._ompa.handle_message, message)

    async def post_tool(self, tool_name: str, tool_input: dict):
        """Async post-tool hook — syncs file writes to palace and KG."""
        return await self._run(self._ompa.post_tool, tool_name, tool_input)

    async def pre_compact(self, transcript: str):
        """Async pre-compact hook — archives session transcript."""
        return await self._run(self._ompa.pre_compact, transcript)

    async def stop(self):
        """Async stop hook — session wrap-up and persist."""
        return await self._run(self._ompa.stop)

    async def wrap_up(self):
        """Alias for stop()."""
        return await self.stop()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    async def search(
        self,
        query: str,
        limit: int = 5,
        hybrid: bool = True,
        wing: str = None,
        room: str = None,
        vaults: list[str] = None,
    ) -> list:
        """Async semantic search across vault(s)."""
        return await self._run(
            self._ompa.search,
            query,
            limit=limit,
            hybrid=hybrid,
            wing=wing,
            room=room,
            vaults=vaults,
        )

    # ------------------------------------------------------------------
    # Classification
    # ------------------------------------------------------------------

    async def classify(self, message: str):
        """Async message classification."""
        return await self._run(self._ompa.classify, message)

    # ------------------------------------------------------------------
    # Knowledge graph
    # ------------------------------------------------------------------

    async def kg_add(
        self,
        subject: str,
        predicate: str,
        object: str,
        valid_from: str = None,
        source: str = None,
    ) -> None:
        """Async KG triple write."""
        return await self._run(
            self._ompa.kg_add,
            subject,
            predicate,
            object,
            valid_from=valid_from,
            source=source,
        )

    async def kg_query(self, entity: str, as_of: str = None) -> list:
        """Async KG entity query."""
        return await self._run(self._ompa.kg_query, entity, as_of=as_of)

    async def kg_timeline(self, entity: str) -> list:
        """Async KG timeline query."""
        return await self._run(self._ompa.kg_timeline, entity)

    async def kg_populate(self) -> int:
        """Async KG population from vault notes."""
        return await self._run(self._ompa.kg_populate)

    # ------------------------------------------------------------------
    # Vault
    # ------------------------------------------------------------------

    async def write(self, content: str, **kwargs) -> dict:
        """Async vault write (auto-classifies in dual-vault mode)."""
        return await self._run(self._ompa.write, content, **kwargs)

    async def get_stats(self) -> dict:
        """Async vault stats."""
        return await self._run(self._ompa.get_stats)

    async def find_orphans(self) -> list:
        """Async orphan detection."""
        return await self._run(self._ompa.find_orphans)

    async def sync(self) -> dict:
        """Async full sync: KG + palace + semantic index."""
        return await self._run(self._ompa.sync)

    async def rebuild_index(self) -> int:
        """Async semantic index rebuild."""
        return await self._run(self._ompa.rebuild_index)

    # ------------------------------------------------------------------
    # Passthrough properties (synchronous access to sub-components)
    # ------------------------------------------------------------------

    @property
    def vault(self):
        return self._ompa.vault

    @property
    def palace(self):
        return self._ompa.palace

    @property
    def kg(self):
        return self._ompa.kg

    @property
    def is_dual_vault(self) -> bool:
        return self._ompa.is_dual_vault

    # ------------------------------------------------------------------
    # Context manager
    # ------------------------------------------------------------------

    async def __aenter__(self) -> AsyncOmpa:
        await self.session_start()
        return self

    async def __aexit__(self, *_) -> None:
        await self.stop()
        self.close()

    def close(self) -> None:
        """Shut down the thread pool."""
        self._executor.shutdown(wait=False)
