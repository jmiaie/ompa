"""
FAISS-backed semantic search index for large OMPA vaults.

Replaces the linear JSON scan in SemanticIndex with a FAISS IVF index,
enabling sub-millisecond approximate nearest-neighbor search over
vaults with tens of thousands of notes.

Requires: pip install ompa[faiss]  (adds faiss-cpu or faiss-gpu)

Usage:
    from ompa.adapters.faiss import FAISSSemanticIndex
    from ompa import Ompa

    # Drop-in replacement for the default SemanticIndex
    index = FAISSSemanticIndex(
        index_path="./.palace/semantic_index",
        model_name="all-MiniLM-L6-v2",
    )
    index.index_vault("./workspace")

    # Or pass a custom NIM backend
    from ompa.adapters.nim import NIMEmbeddingBackend
    backend = NIMEmbeddingBackend.from_env()
    index = FAISSSemanticIndex(
        index_path="./.palace/faiss_index",
        embedding_backend=backend,
        embedding_dim=1024,   # match NIM model output dim
    )
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from ..semantic import EmbeddingBackend, SearchResult

logger = logging.getLogger(__name__)

_FAISS_INDEX_FILE = "faiss.index"
_META_FILE = "faiss_meta.json"


class FAISSSemanticIndex:
    """
    FAISS-backed drop-in replacement for SemanticIndex.

    Uses a flat L2 index by default (exact search, reliable for <100K docs).
    Set use_ivf=True for approximate search on larger corpora (requires training).

    The same save/load/index_vault/search interface as SemanticIndex —
    can be swapped in anywhere SemanticIndex is used.
    """

    def __init__(
        self,
        index_path: Path,
        model_name: str = "all-MiniLM-L6-v2",
        embedding_dim: int = 384,
        embedding_backend: EmbeddingBackend | None = None,
        use_ivf: bool = False,
        ivf_nlist: int = 100,
    ):
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self.embedding_dim = embedding_dim
        self.use_ivf = use_ivf
        self.ivf_nlist = ivf_nlist

        self._embedding_backend = embedding_backend
        self._model = None
        self._initialized = False

        self._faiss_index = None  # faiss.Index
        self._metadata: list[dict] = []  # parallel list: one dict per vector

    # ------------------------------------------------------------------
    # Model / backend init
    # ------------------------------------------------------------------

    def _ensure_model(self) -> bool:
        if self._initialized:
            return self._model is not None or self._embedding_backend is not None

        if self._embedding_backend is not None:
            self._initialized = True
            return True

        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            self._initialized = True
            return True
        except ImportError:
            logger.warning(
                "sentence-transformers not installed. Install with: pip install ompa[semantic]"
            )
        except Exception as e:
            logger.warning("Could not load embedding model: %s", e)

        self._initialized = True
        return False

    def _encode(self, text: str) -> list[float]:
        if self._embedding_backend:
            return self._embedding_backend.encode(text)
        if self._model:
            return self._model.encode(text).tolist()
        return []

    # ------------------------------------------------------------------
    # FAISS index management
    # ------------------------------------------------------------------

    def _require_faiss(self):
        try:
            import faiss

            return faiss
        except ImportError:
            raise ImportError(
                "faiss-cpu (or faiss-gpu) is required. Install with: pip install ompa[faiss]"
            ) from None

    def _build_index(self) -> None:
        """Create or reset the FAISS index."""
        faiss = self._require_faiss()

        if self.use_ivf and len(self._metadata) >= self.ivf_nlist * 39:
            quantizer = faiss.IndexFlatL2(self.embedding_dim)
            self._faiss_index = faiss.IndexIVFFlat(quantizer, self.embedding_dim, self.ivf_nlist)
        else:
            self._faiss_index = faiss.IndexFlatIP(
                self.embedding_dim
            )  # inner product ≈ cosine for normalized vecs

    def _add_vector(self, embedding: list[float]) -> None:
        import numpy as np

        faiss = self._require_faiss()

        vec = np.array([embedding], dtype="float32")
        faiss.normalize_L2(vec)  # normalize for cosine similarity via inner product

        if self._faiss_index is None:
            self._build_index()

        if self.use_ivf and not getattr(self._faiss_index, "is_trained", True):
            return  # deferred — train after collecting enough vectors

        self._faiss_index.add(vec)  # type: ignore[attr-defined]

    def _train_if_needed(self) -> None:
        if not self.use_ivf or self._faiss_index is None:
            return
        if getattr(self._faiss_index, "is_trained", True):
            return

        import numpy as np

        faiss = self._require_faiss()

        if len(self._metadata) < self.ivf_nlist:
            # Not enough vectors to train — fall back to flat
            self._build_index()
            return

        vecs = np.array([m["embedding"] for m in self._metadata], dtype="float32")
        faiss.normalize_L2(vecs)
        self._faiss_index.train(vecs)  # type: ignore[union-attr]
        self._faiss_index.add(vecs)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Indexing
    # ------------------------------------------------------------------

    def index_file(self, path: Path) -> None:
        """Index a single markdown file."""
        if not path.exists() or path.suffix != ".md":
            return

        if not self._ensure_model():
            return

        path_str = str(path)
        self._metadata = [m for m in self._metadata if m["path"] != path_str]

        try:
            content = path.read_text(encoding="utf-8")
            chunk_size = 512
            words = content.split()

            for i in range(0, len(words), chunk_size):
                chunk = " ".join(words[i : i + chunk_size])
                if len(chunk.strip()) < 20:
                    continue

                embedding = self._encode(chunk)
                if not embedding:
                    continue

                self._metadata.append(
                    {
                        "path": path_str,
                        "chunk_index": i,
                        "text": chunk,
                        "embedding": embedding,
                    }
                )
        except Exception as e:
            logger.warning("Error indexing %s: %s", path, e)

    def index_vault(self, vault_path: Path, exclude_patterns: list = None) -> int:
        """Index all markdown files in a vault. Returns file count."""
        from ..vault import DEFAULT_EXCLUDE_PATTERNS

        exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS
        count = 0
        self._metadata = []

        if not self._ensure_model():
            return 0

        for path in Path(vault_path).rglob("*.md"):
            if any(excl in str(path) for excl in exclude_patterns):
                continue
            self.index_file(path)
            count += 1

        self._rebuild_faiss()
        return count

    def _rebuild_faiss(self) -> None:
        """Rebuild the FAISS index from all stored metadata."""
        import numpy as np

        faiss = self._require_faiss()

        self._build_index()
        if not self._metadata:
            return

        vecs = np.array([m["embedding"] for m in self._metadata], dtype="float32")
        faiss.normalize_L2(vecs)

        if self.use_ivf:
            if len(self._metadata) >= self.ivf_nlist:
                self._faiss_index.train(vecs)  # type: ignore[attr-defined]
            else:
                self._faiss_index = faiss.IndexFlatIP(self.embedding_dim)
        if self._faiss_index is not None:
            self._faiss_index.add(vecs)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, limit: int = 5, hybrid: bool = True) -> list[SearchResult]:
        """Search using FAISS ANN + optional keyword boost."""
        import numpy as np

        faiss = self._require_faiss()

        if not self._ensure_model() or not self._metadata or self._faiss_index is None:
            return self._keyword_fallback(query, limit)

        try:
            q_vec = self._encode(query)
            if not q_vec:
                return self._keyword_fallback(query, limit)

            q_arr = np.array([q_vec], dtype="float32")
            faiss.normalize_L2(q_arr)

            k = min(limit * 3, len(self._metadata))
            scores, indices = self._faiss_index.search(q_arr, k)

            results = []
            seen_paths = set()

            for score, idx in zip(scores[0], indices[0], strict=False):
                if idx < 0 or idx >= len(self._metadata):
                    continue

                meta = self._metadata[idx]
                path = meta["path"]

                if path in seen_paths:
                    continue
                seen_paths.add(path)

                final_score = float(score)
                match_type = "semantic"

                if hybrid:
                    query_words = set(query.lower().split())
                    chunk_words = set(meta["text"].lower().split())
                    overlap = query_words & chunk_words
                    if overlap:
                        boost = len(overlap) / len(query_words) * 0.3
                        final_score += boost
                        match_type = "hybrid"

                text = meta["text"]
                results.append(
                    SearchResult(
                        path=path,
                        content_excerpt=text[:300] + "..." if len(text) > 300 else text,
                        score=final_score,
                        match_type=match_type,
                    )
                )

                if len(results) >= limit:
                    break

            results.sort(key=lambda r: r.score, reverse=True)
            return results

        except Exception as e:
            logger.warning("FAISS search failed, falling back to keyword: %s", e)
            return self._keyword_fallback(query, limit)

    def _keyword_fallback(self, query: str, limit: int) -> list[SearchResult]:
        query_lower = query.lower()
        results = []
        seen = set()
        for m in self._metadata:
            if query_lower in m["text"].lower() and m["path"] not in seen:
                seen.add(m["path"])
                results.append(
                    SearchResult(
                        path=m["path"],
                        content_excerpt=m["text"][:200],
                        score=1.0,
                        match_type="keyword",
                    )
                )
            if len(results) >= limit:
                break
        return results

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_index(self) -> None:
        faiss = self._require_faiss()

        if self._faiss_index is not None:
            faiss.write_index(
                self._faiss_index,
                str(self.index_path / _FAISS_INDEX_FILE),
            )

        meta_file = self.index_path / _META_FILE
        with meta_file.open("w", encoding="utf-8") as f:
            json.dump(
                {
                    "model": self.model_name,
                    "embedding_dim": self.embedding_dim,
                    "count": len(self._metadata),
                    "chunks": [
                        {k: v for k, v in m.items() if k != "embedding"} for m in self._metadata
                    ],
                },
                f,
            )

        emb_file = self.index_path / "embeddings.npy"
        try:
            import numpy as np

            np.save(
                str(emb_file), np.array([m["embedding"] for m in self._metadata], dtype="float32")
            )
        except Exception as e:
            logger.warning("Could not save embeddings: %s", e)

    def load_index(self) -> bool:
        faiss_file = self.index_path / _FAISS_INDEX_FILE
        meta_file = self.index_path / _META_FILE
        emb_file = self.index_path / "embeddings.npy"

        if not faiss_file.exists() or not meta_file.exists():
            return False

        try:
            faiss = self._require_faiss()
            self._faiss_index = faiss.read_index(str(faiss_file))

            with meta_file.open(encoding="utf-8") as f:
                data = json.load(f)

            chunks = data.get("chunks", [])

            if emb_file.exists():
                import numpy as np

                embeddings = np.load(str(emb_file)).tolist()
                for chunk, emb in zip(chunks, embeddings, strict=False):
                    chunk["embedding"] = emb
            else:
                for chunk in chunks:
                    chunk["embedding"] = []

            self._metadata = chunks
            self._initialized = True
            return True
        except Exception as e:
            logger.warning("Could not load FAISS index: %s", e)
            return False

    def clear(self) -> None:
        self._faiss_index = None
        self._metadata = []
        for f in [_FAISS_INDEX_FILE, _META_FILE, "embeddings.npy"]:
            p = self.index_path / f
            if p.exists():
                p.unlink()
