"""
NVIDIA NIM embeddings backend for OMPA.

Replaces sentence-transformers with NVIDIA's NIM inference API for embedding
generation — useful for nodes with NVIDIA GPUs (Jarv/Kai/Tai) or for
enterprise deployments that want GPU-accelerated embeddings without the
local model download overhead.

Usage:
    pip install ompa httpx

    from ompa.adapters.nim import NIMEmbeddingBackend
    from ompa import Ompa

    backend = NIMEmbeddingBackend(
        api_key="nvapi-...",
        model="nvidia/nv-embedqa-e5-v5",
    )
    ao = Ompa(vault_path="./workspace", embedding_backend=backend)

Or wire directly into SemanticIndex:
    from ompa.semantic import SemanticIndex
    from ompa.adapters.nim import NIMEmbeddingBackend

    backend = NIMEmbeddingBackend(api_key="nvapi-...")
    index = SemanticIndex(index_path="./.palace/semantic_index", embedding_backend=backend)
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Default NIM embeddings endpoint (NVIDIA API catalog)
_NIM_DEFAULT_ENDPOINT = "https://integrate.api.nvidia.com/v1/embeddings"
_NIM_DEFAULT_MODEL = "nvidia/nv-embedqa-e5-v5"


class NIMEmbeddingBackend:
    """
    NVIDIA NIM embeddings backend.

    Implements the EmbeddingBackend protocol (encode method) so it can be
    used as a drop-in replacement for sentence-transformers inside SemanticIndex.

    Supports:
    - NVIDIA API Catalog (api.nvidia.com) — set api_key
    - Self-hosted NIM containers — set endpoint_url to your container address
    - Batched encoding with configurable batch_size
    - LRU cache for repeated identical texts

    Example with self-hosted NIM:
        backend = NIMEmbeddingBackend(
            api_key="not-required-for-local",
            endpoint_url="http://localhost:8000/v1/embeddings",
            model="nvidia/nv-embedqa-e5-v5",
        )
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = _NIM_DEFAULT_MODEL,
        endpoint_url: str = _NIM_DEFAULT_ENDPOINT,
        batch_size: int = 32,
        timeout: float = 30.0,
        input_type: str = "query",  # "query" or "passage"
        truncate: str = "END",
    ):
        self.api_key = api_key or os.environ.get("NVIDIA_API_KEY", "")
        self.model = model
        self.endpoint_url = endpoint_url
        self.batch_size = batch_size
        self.timeout = timeout
        self.input_type = input_type
        self.truncate = truncate
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import httpx

                self._client = httpx.Client(timeout=self.timeout)
            except ImportError:
                raise ImportError(
                    "httpx is required for the NIM backend. Install with: pip install ompa[nim]"
                ) from None
        return self._client

    def encode(self, text: str) -> list[float]:
        """
        Encode a single text string to an embedding vector.

        This is the EmbeddingBackend protocol method — compatible with
        SemanticIndex's embedding_backend parameter.
        """
        results = self.encode_batch([text])
        return results[0] if results else []

    def encode_batch(self, texts: list[str]) -> list[list[float]]:
        """Encode a batch of texts. Returns list of embedding vectors."""
        if not texts:
            return []

        client = self._get_client()
        all_embeddings = []

        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]
            embeddings = self._call_api(client, batch)
            all_embeddings.extend(embeddings)

        return all_embeddings

    def _call_api(self, client, texts: list[str]) -> list[list[float]]:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        payload = {
            "input": texts,
            "model": self.model,
            "input_type": self.input_type,
            "truncate": self.truncate,
        }

        try:
            response = client.post(self.endpoint_url, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
            # NIM API returns {"data": [{"embedding": [...], "index": N}, ...]}
            items = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in items]
        except Exception as e:
            logger.error("NIM API call failed: %s", e)
            raise

    def close(self) -> None:
        """Close the underlying HTTP client."""
        if self._client:
            self._client.close()
            self._client = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @classmethod
    def from_env(cls, **kwargs) -> NIMEmbeddingBackend:
        """
        Create a NIMEmbeddingBackend from environment variables.

        Reads:
            NVIDIA_API_KEY     — API key
            NVIDIA_NIM_ENDPOINT — endpoint URL (optional, defaults to api.nvidia.com)
            NVIDIA_NIM_MODEL   — model name (optional)
        """
        return cls(
            api_key=os.environ.get("NVIDIA_API_KEY"),
            endpoint_url=os.environ.get("NVIDIA_NIM_ENDPOINT", _NIM_DEFAULT_ENDPOINT),
            model=os.environ.get("NVIDIA_NIM_MODEL", _NIM_DEFAULT_MODEL),
            **kwargs,
        )

    def __repr__(self) -> str:
        masked_key = f"{self.api_key[:8]}..." if len(self.api_key) > 8 else "***"
        return f"NIMEmbeddingBackend(model={self.model!r}, endpoint={self.endpoint_url!r}, key={masked_key})"
