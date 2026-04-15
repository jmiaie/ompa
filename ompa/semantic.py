"""
Semantic search for OMPA.
Provides hybrid keyword + semantic search across the vault.

Storage layout (v2, since 0.5.0):
    <index_path>/semantic_index.meta.json   — chunk metadata (path, text, hash)
    <index_path>/semantic_index.embeddings.npy — N×D float32 matrix

The old format (<index_path>/semantic_index.json, all-in-one JSON with
``embedding`` lists inside each chunk) is auto-migrated on first ``load_index``.
"""

import json
import logging
import hashlib
from pathlib import Path
from dataclasses import dataclass

from .vault import DEFAULT_EXCLUDE_PATTERNS

logger = logging.getLogger(__name__)


def _np():
    """Import numpy lazily so merely importing this module stays cheap."""
    import numpy as np

    return np


@dataclass
class SearchResult:
    path: str
    content_excerpt: str
    score: float
    match_type: str  # "semantic", "keyword", "hybrid"


class SemanticIndex:
    """
    Semantic search index for the vault.
    Uses local embeddings (sentence-transformers) for zero API cost.

    Falls back to keyword search if embeddings not available.

    Search is O(N) matrix-vector: embeddings are stored as one
    ``(N, embedding_dim)`` float32 matrix and scored with a single numpy
    dot product per query, rather than a Python for-loop over chunks.
    """

    # File names for the v2 storage format.
    META_FILE = "semantic_index.meta.json"
    EMB_FILE = "semantic_index.embeddings.npy"
    # Legacy all-in-one JSON (auto-migrated on load).
    LEGACY_FILE = "semantic_index.json"

    def __init__(
        self,
        index_path: Path,
        model_name: str = "all-MiniLM-L6-v2",
        embedding_dim: int = 384,
    ):
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self.embedding_dim = embedding_dim
        # chunks[i] and self.embeddings[i] are row-aligned.
        self.chunks: list[dict] = []
        self.embeddings = None  # (N, embedding_dim) float32 matrix or None
        self._initialized = False
        self._model = None

    @property
    def model(self):
        """Lazy-load the model on first access."""
        if self._model is None:
            self._init_model()
        return self._model

    def _init_model(self) -> None:
        """Initialize the embedding model (called lazily)."""
        if self._initialized:
            return
        try:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name)
            self._initialized = True
        except ImportError:
            logger.warning(
                "sentence-transformers not installed. Semantic search unavailable. "
                "Install with: pip install ompa[semantic]"
            )
            self._model = None
        except Exception as e:
            logger.warning("Could not load embedding model: %s", e)
            self._model = None
        self._initialized = True

    # -------------------------------------------------------------------------
    # Internal matrix helpers
    # -------------------------------------------------------------------------

    def _embeddings_for_indexes(self, indexes: list[int]):
        """Return the sub-matrix of ``self.embeddings`` for the given rows.
        Used when filtering out chunks for a file during incremental update.
        """
        if self.embeddings is None or not indexes:
            return None
        np = _np()
        return self.embeddings[np.asarray(indexes, dtype=np.int64)]

    def _rebuild_embeddings_from_rows(self, rows: list) -> None:
        """Replace ``self.embeddings`` with the given row list/iterable.
        Accepts numpy arrays or Python lists of floats (for old-format load).
        """
        np = _np()
        if not rows:
            self.embeddings = None
            return
        self.embeddings = np.asarray(rows, dtype=np.float32)

    # -------------------------------------------------------------------------
    # Indexing
    # -------------------------------------------------------------------------

    def index_file(self, path: Path) -> None:
        """Index a single file (or re-index if already present).

        All new chunks for the file are encoded in a single batched
        ``model.encode`` call so the sentence-transformers model runs its
        forward pass once instead of once per chunk.
        """
        if not path.exists():
            return

        if not self._initialized:
            self._init_model()
        if self._model is None:
            return

        try:
            path_str = str(path)

            # Drop any existing chunks (and their embedding rows) for this path.
            keep_indexes = [
                i for i, c in enumerate(self.chunks) if c["path"] != path_str
            ]
            new_chunks = [self.chunks[i] for i in keep_indexes]
            new_embeddings = self._embeddings_for_indexes(keep_indexes)

            content = path.read_text(encoding="utf-8")
            # Split into chunks (512 words each — matches prior behavior).
            chunk_size = 512
            words = content.split()

            chunk_texts: list[str] = []
            chunk_records: list[dict] = []
            for i in range(0, len(words), chunk_size):
                chunk_text = " ".join(words[i : i + chunk_size])
                if len(chunk_text.strip()) < 20:
                    continue
                chunk_hash = hashlib.sha256(f"{path}:{i}".encode()).hexdigest()[:16]
                chunk_texts.append(chunk_text)
                chunk_records.append(
                    {
                        "hash": chunk_hash,
                        "path": path_str,
                        "chunk_index": i,
                        "text": chunk_text,
                    }
                )

            if chunk_texts:
                # Single batched forward pass for every chunk in this file.
                np = _np()
                new_vecs = self._model.encode(
                    chunk_texts,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
                new_vecs = np.asarray(new_vecs, dtype=np.float32)

                if new_embeddings is None:
                    combined_vecs = new_vecs
                else:
                    combined_vecs = np.vstack([new_embeddings, new_vecs])
                self.embeddings = combined_vecs
                self.chunks = new_chunks + chunk_records
            else:
                # No new chunks survived the 20-char filter — just drop the old ones.
                self.chunks = new_chunks
                self.embeddings = new_embeddings

        except Exception as e:
            logger.warning("Error indexing %s: %s", path, e)

    def update_file(self, path: Path) -> bool:
        """Incrementally update the index for a single file + save."""
        path = Path(path)
        if not path.exists() or path.suffix != ".md":
            return False

        try:
            self.index_file(path)
            self.save_index()
            logger.debug("Incrementally updated index for %s", path)
            return True
        except Exception as e:
            logger.warning("Incremental index update failed for %s: %s", path, e)
            return False

    def remove_file(self, path: Path) -> bool:
        """Remove a file from the index (e.g., after deletion)."""
        path_str = str(Path(path))
        keep_indexes = [i for i, c in enumerate(self.chunks) if c["path"] != path_str]
        if len(keep_indexes) == len(self.chunks):
            return False
        new_chunks = [self.chunks[i] for i in keep_indexes]
        new_embeddings = self._embeddings_for_indexes(keep_indexes)
        self.chunks = new_chunks
        self.embeddings = new_embeddings
        self.save_index()
        logger.debug(
            "Removed %d chunks for %s",
            len(self.chunks) - len(new_chunks),
            path,
        )
        return True

    def index_vault(self, vault_path: Path, exclude_patterns: list = None) -> int:
        """Index all markdown files in a vault."""
        exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS
        count = 0

        if not self._initialized:
            self._init_model()
        if self._model is None:
            return 0

        for path in vault_path.rglob("*.md"):
            if any(excl in str(path) for excl in exclude_patterns):
                continue
            self.index_file(path)
            count += 1

        return count

    # -------------------------------------------------------------------------
    # Persistence (v2 binary format + legacy JSON migration)
    # -------------------------------------------------------------------------

    def save_index(self) -> None:
        """Save the index using the v2 binary format.

        Writes two files:
          * ``semantic_index.meta.json`` — chunk metadata only
          * ``semantic_index.embeddings.npy`` — float32 matrix
        """
        np = _np()
        meta_file = self.index_path / self.META_FILE
        emb_file = self.index_path / self.EMB_FILE

        meta = {
            "model": self.model_name,
            "embedding_dim": self.embedding_dim,
            "version": 2,
            "chunks": self.chunks,
        }
        with open(meta_file, "w", encoding="utf-8") as f:
            json.dump(meta, f)

        if self.embeddings is None or len(self.embeddings) == 0:
            # Keep on-disk state consistent with in-memory state.
            if emb_file.exists():
                emb_file.unlink()
            return

        np.save(emb_file, np.asarray(self.embeddings, dtype=np.float32))

    def load_index(self) -> bool:
        """Load the index, migrating the legacy single-JSON format if needed."""
        meta_file = self.index_path / self.META_FILE
        emb_file = self.index_path / self.EMB_FILE
        legacy_file = self.index_path / self.LEGACY_FILE

        # Prefer v2 (meta + .npy) when present.
        if meta_file.exists():
            try:
                with open(meta_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.chunks = data.get("chunks", [])
                if emb_file.exists() and self.chunks:
                    np = _np()
                    self.embeddings = np.load(emb_file)
                else:
                    self.embeddings = None
                return True
            except Exception as e:
                logger.warning("Error loading v2 index: %s", e)
                return False

        # Legacy: single JSON with per-chunk embedding arrays.
        if legacy_file.exists():
            try:
                with open(legacy_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                raw_chunks = data.get("chunks", [])
                rows = []
                stripped_chunks = []
                for c in raw_chunks:
                    emb = c.get("embedding")
                    if emb is None:
                        continue
                    rows.append(emb)
                    # Strip the embedding field from the chunk metadata —
                    # from now on it lives in the .npy matrix.
                    stripped = {k: v for k, v in c.items() if k != "embedding"}
                    stripped_chunks.append(stripped)
                self.chunks = stripped_chunks
                self._rebuild_embeddings_from_rows(rows)
                # Persist in the new format and remove the legacy file so
                # subsequent loads skip the migration path.
                self.save_index()
                try:
                    legacy_file.unlink()
                except OSError:
                    pass
                logger.info(
                    "Migrated legacy semantic index (%d chunks) to v2 binary format",
                    len(self.chunks),
                )
                return True
            except Exception as e:
                logger.warning("Error migrating legacy index: %s", e)
                return False

        return False

    # -------------------------------------------------------------------------
    # Search
    # -------------------------------------------------------------------------

    def search(
        self,
        query: str,
        limit: int = 5,
        hybrid: bool = True,
    ) -> list[SearchResult]:
        """Search the vault.

        Scoring is a single numpy dot product: each chunk's cosine similarity
        is computed in parallel, then best-per-path dedup returns the top
        ``limit`` distinct files. A file's best chunk wins its slot.
        """
        limit = min(limit, 100)  # Cap to prevent DoS

        if not self._initialized or not self.chunks or self.embeddings is None:
            return self._keyword_search(query, limit)

        try:
            np = _np()
            query_vec = self._model.encode(
                query,
                convert_to_numpy=True,
                show_progress_bar=False,
            )
            query_vec = np.asarray(query_vec, dtype=np.float32)

            # Cosine similarity = (A · q) / (|A_i| * |q|).
            q_norm = float(np.linalg.norm(query_vec))
            if q_norm == 0.0:
                return self._keyword_search(query, limit)
            chunk_norms = np.linalg.norm(self.embeddings, axis=1)
            # Guard against zero-norm rows (shouldn't happen but protects
            # against divide-by-zero if corrupted data slips in).
            chunk_norms = np.where(chunk_norms == 0.0, 1.0, chunk_norms)
            dots = self.embeddings @ query_vec
            sims = dots / (chunk_norms * q_norm)

            # Optional keyword boost (hybrid mode).
            query_lower = query.lower()
            query_words = set(query_lower.split())
            boosted_hits = 0

            # Keep the best score seen per path; also remember which chunk
            # produced it so the excerpt is from the winning chunk.
            best_per_path: dict[str, tuple[float, int, bool]] = {}
            # (score, chunk_idx, had_keyword_overlap)

            for idx, chunk in enumerate(self.chunks):
                score = float(sims[idx])
                had_overlap = False
                if hybrid and query_words:
                    chunk_words = set(chunk["text"].lower().split())
                    overlap = query_words & chunk_words
                    if overlap:
                        had_overlap = True
                        boosted_hits += 1
                        score += len(overlap) / len(query_words) * 0.3

                existing = best_per_path.get(chunk["path"])
                if existing is None or score > existing[0]:
                    best_per_path[chunk["path"]] = (score, idx, had_overlap)

            # Sort the per-path bests and take the top ``limit``.
            ranked = sorted(
                best_per_path.values(),
                key=lambda v: v[0],
                reverse=True,
            )[:limit]

            results: list[SearchResult] = []
            for score, chunk_idx, had_overlap in ranked:
                chunk = self.chunks[chunk_idx]
                excerpt = (
                    chunk["text"][:300] + "..."
                    if len(chunk["text"]) > 300
                    else chunk["text"]
                )
                results.append(
                    SearchResult(
                        path=chunk["path"],
                        content_excerpt=excerpt,
                        score=score,
                        match_type="hybrid" if hybrid and had_overlap else "semantic",
                    )
                )
            return results

        except Exception as e:
            logger.warning("Search error: %s", e)
            return self._keyword_search(query, limit)

    def _keyword_search(self, query: str, limit: int) -> list[SearchResult]:
        """Fallback keyword search using pure Python (no subprocess)."""
        query_lower = query.lower()
        results = []

        # Search through indexed chunks first
        if self.chunks:
            for chunk in self.chunks:
                if query_lower in chunk["text"].lower():
                    results.append(
                        SearchResult(
                            path=chunk["path"],
                            content_excerpt=chunk["text"][:200],
                            score=1.0,
                            match_type="keyword",
                        )
                    )
                    if len(results) >= limit:
                        break
            return results

        # Fallback: scan the vault directory
        vault_path = self.index_path.parent.parent  # .palace/semantic_index -> vault
        if vault_path.exists():
            for md_file in vault_path.rglob("*.md"):
                if any(excl in str(md_file) for excl in DEFAULT_EXCLUDE_PATTERNS):
                    continue
                try:
                    content = md_file.read_text(encoding="utf-8")
                    for line_content in content.split("\n"):
                        if query_lower in line_content.lower():
                            results.append(
                                SearchResult(
                                    path=str(md_file),
                                    content_excerpt=line_content[:200],
                                    score=1.0,
                                    match_type="keyword",
                                )
                            )
                            break
                except Exception as e:
                    logger.debug("Skipping %s: %s", md_file, e)
                if len(results) >= limit:
                    break

        return results

    def clear(self) -> None:
        """Clear the index (both in-memory and on disk)."""
        self.chunks = []
        self.embeddings = None
        for fname in (self.META_FILE, self.EMB_FILE, self.LEGACY_FILE):
            p = self.index_path / fname
            if p.exists():
                try:
                    p.unlink()
                except OSError:
                    pass


def qmd_query(vault_path: str, query: str, limit: int = 5) -> list[SearchResult]:
    """
    Convenience function for QMD-style queries.
    Mimics the qmd CLI behavior.
    """
    index_path = Path(vault_path) / ".palace" / "semantic_index"
    index = SemanticIndex(index_path)
    index.load_index()
    return index.search(query, limit)
