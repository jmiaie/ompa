"""
Semantic search for OMPA.
Provides hybrid keyword + semantic search across the vault.
"""

import json
import logging
import hashlib
from pathlib import Path
from dataclasses import dataclass

from .vault import DEFAULT_EXCLUDE_PATTERNS

logger = logging.getLogger(__name__)


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
    """

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
        self.embeddings = None
        self.chunks = []
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

    def index_file(self, path: Path) -> None:
        """Index a single file (or re-index if already present)."""
        if not self._initialized or not path.exists():
            return

        try:
            # Remove existing chunks for this file (incremental update)
            path_str = str(path)
            self.chunks = [c for c in self.chunks if c["path"] != path_str]

            content = path.read_text(encoding="utf-8")
            # Split into chunks (512 tokens each)
            chunk_size = 512
            words = content.split()

            for i in range(0, len(words), chunk_size):
                chunk_text = " ".join(words[i : i + chunk_size])
                if len(chunk_text.strip()) < 20:
                    continue

                embedding = self.model.encode(chunk_text)
                chunk_hash = hashlib.sha256(f"{path}:{i}".encode()).hexdigest()[:16]

                self.chunks.append(
                    {
                        "hash": chunk_hash,
                        "path": path_str,
                        "chunk_index": i,
                        "text": chunk_text,
                        "embedding": embedding.tolist(),
                    }
                )
        except Exception as e:
            logger.warning("Error indexing %s: %s", path, e)

    def update_file(self, path: Path) -> bool:
        """
        Incrementally update the index for a single file.
        Re-indexes the file and saves the updated index.
        Returns True if successful.
        """
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
        before = len(self.chunks)
        self.chunks = [c for c in self.chunks if c["path"] != path_str]
        removed = before - len(self.chunks)
        if removed > 0:
            self.save_index()
            logger.debug("Removed %d chunks for %s", removed, path)
        return removed > 0

    def index_vault(self, vault_path: Path, exclude_patterns: list = None) -> int:
        """Index all markdown files in a vault."""
        exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS
        count = 0

        if not self._initialized:
            return 0

        for path in vault_path.rglob("*.md"):
            if any(excl in str(path) for excl in exclude_patterns):
                continue
            self.index_file(path)
            count += 1

        return count

    def save_index(self) -> None:
        """Save the index to disk."""
        index_file = self.index_path / "semantic_index.json"

        serializable = {
            "model": self.model_name,
            "chunks": [{**c, "embedding": c["embedding"]} for c in self.chunks],
        }

        with open(index_file, "w", encoding="utf-8") as f:
            json.dump(serializable, f)

    def load_index(self) -> bool:
        """Load the index from disk."""
        index_file = self.index_path / "semantic_index.json"
        if not index_file.exists():
            return False

        try:
            with open(index_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.chunks = data["chunks"]
            return True
        except Exception as e:
            logger.warning("Error loading index: %s", e)
            return False

    def search(
        self,
        query: str,
        limit: int = 5,
        hybrid: bool = True,
    ) -> list[SearchResult]:
        """
        Search the vault.

        Args:
            query: Search query
            limit: Max results (capped at 100)
            hybrid: Use both semantic + keyword if True, semantic only if False

        Returns:
            List of SearchResult
        """
        limit = min(limit, 100)  # Cap to prevent DoS

        if not self._initialized or not self.chunks:
            return self._keyword_search(query, limit)

        try:
            query_embedding = self.model.encode(query)

            from sentence_transformers import util

            best_results = []

            for chunk in self.chunks:
                # Semantic similarity
                chunk_embedding = chunk["embedding"]
                similarity = util.cos_sim(query_embedding, chunk_embedding)[0][0].item()

                # Keyword boost
                keyword_boost = 0.0
                if hybrid:
                    query_lower = query.lower()
                    chunk_lower = chunk["text"].lower()
                    query_words = set(query_lower.split())
                    chunk_words = set(chunk_lower.split())
                    overlap = query_words & chunk_words
                    if overlap:
                        keyword_boost = len(overlap) / len(query_words) * 0.3

                combined_score = similarity + keyword_boost

                best_results.append(
                    SearchResult(
                        path=chunk["path"],
                        content_excerpt=(
                            chunk["text"][:300] + "..."
                            if len(chunk["text"]) > 300
                            else chunk["text"]
                        ),
                        score=combined_score,
                        match_type=(
                            "hybrid" if hybrid and keyword_boost > 0 else "semantic"
                        ),
                    )
                )

            # Sort by score and dedupe by path
            best_results.sort(key=lambda r: r.score, reverse=True)

            seen_paths = set()
            unique_results = []
            for result in best_results:
                if result.path not in seen_paths:
                    seen_paths.add(result.path)
                    unique_results.append(result)
                    if len(unique_results) >= limit:
                        break

            return unique_results

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
        """Clear the index."""
        self.chunks = []
        index_file = self.index_path / "semantic_index.json"
        if index_file.exists():
            index_file.unlink()


def qmd_query(vault_path: str, query: str, limit: int = 5) -> list[SearchResult]:
    """
    Convenience function for QMD-style queries.
    Mimics the qmd CLI behavior.
    """
    index_path = Path(vault_path) / ".palace" / "semantic_index"
    index = SemanticIndex(index_path)
    index.load_index()
    return index.search(query, limit)
