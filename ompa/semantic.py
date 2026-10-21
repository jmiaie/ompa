"""
Semantic search for OMPA.
Provides semantic search across the vault, and hybrid semantic + BM25
(reciprocal rank fusion) when a :class:`~ompa.keyword.KeywordIndex` is attached.
Supports a pluggable embedding backend (sentence-transformers by default,
or any object with an encode(text: str) -> array interface).
"""

import hashlib
import json
import logging
import math
import os
import struct
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from .vault import DEFAULT_EXCLUDE_PATTERNS

logger = logging.getLogger(__name__)


@runtime_checkable
class EmbeddingBackend(Protocol):
    """Any object with an encode(text) method can serve as an embedding backend.

    Backends may also offer ``encode_batch(texts) -> list[vector]``; it is used
    for bulk indexing when present.
    """

    def encode(self, text: str) -> "list[float]": ...


try:  # numpy is optional (it ships with the ``semantic`` extra)
    import numpy as _np
except ImportError:  # pragma: no cover - exercised only without numpy
    _np = None

# On-disk format (v2): small JSON metadata + one float16 matrix (rows = chunks).
INDEX_FORMAT_VERSION = 2
META_FILE = "index_meta.json"
VECTORS_FILE = "index_vectors.f16"
LEGACY_FILE = "semantic_index.json"  # v1: everything, embeddings as JSON floats

EXCERPT_CHARS = 300
CHUNK_WORDS = 512
RRF_K = 60  # reciprocal-rank-fusion constant (Cormack et al. 2009)

_MODEL_CACHE: dict[str, Any] = {}
_MODEL_LOCK = threading.Lock()


def _load_sentence_transformer(model_name: str):
    """One SentenceTransformer per model name per process (they are large)."""
    with _MODEL_LOCK:
        model = _MODEL_CACHE.get(model_name)
        if model is None:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(model_name)
            _MODEL_CACHE[model_name] = model
        return model


def _cosine_similarity(a, b) -> float:
    """Cosine similarity of two vectors (numpy if available, else pure Python)."""
    try:
        va, vb = _as_vector(a), _as_vector(b)
        if _np is not None:
            norm = float(_np.linalg.norm(va) * _np.linalg.norm(vb))
            return float(_np.dot(va, vb) / norm) if norm > 1e-9 else 0.0
        norm = math.sqrt(sum(x * x for x in va)) * math.sqrt(sum(x * x for x in vb))
        return sum(x * y for x, y in zip(va, vb, strict=False)) / norm if norm > 1e-9 else 0.0
    except Exception:
        return 0.0


def _as_vector(e):
    """Embedding (ndarray, tensor, list, ...) -> flat float32 ndarray or list[float]."""
    if hasattr(e, "tolist") and not isinstance(e, list):
        e = e.tolist()
    if _np is not None:
        return _np.asarray(e, dtype=_np.float32).ravel()
    return [float(x) for x in e]


def _normalized(e):
    v = _as_vector(e)
    if _np is not None:
        n = float(_np.linalg.norm(v))
        return v / n if n > 1e-12 else v
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v] if n > 1e-12 else v


def _file_signature(path: Path) -> list[int]:
    """Cheap change detector for a note: [mtime_ns, size_bytes]."""
    st = path.stat()
    return [st.st_mtime_ns, st.st_size]


def scan_vault(vault_path: Path, exclude_patterns: list | None = None) -> dict[str, list[int]]:
    """``{path: [st_mtime_ns, st_size]}`` for every indexable note (one directory walk).

    Pass the result as ``snapshot=`` to :meth:`SemanticIndex.refresh` and
    :meth:`ompa.keyword.KeywordIndex.refresh` so both share a single walk.
    """
    exclude_patterns = exclude_patterns or DEFAULT_EXCLUDE_PATTERNS
    out: dict[str, list[int]] = {}
    for path in Path(vault_path).rglob("*.md"):
        path_str = str(path)
        if any(excl in path_str for excl in exclude_patterns):
            continue
        try:
            out[path_str] = _file_signature(path)
        except OSError:
            continue
    return out


def _chunk_texts(content: str) -> list[tuple[int, str]]:
    words = content.split()
    out = []
    for i in range(0, len(words), CHUNK_WORDS):
        text = " ".join(words[i : i + CHUNK_WORDS])
        if len(text.strip()) >= 20:
            out.append((i, text))
    return out


def _excerpt(text: str, n: int = EXCERPT_CHARS) -> str:
    return text[:n] + "..." if len(text) > n else text


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)


class _VectorStore:
    """Resident chunk vectors (L2-normalised) with O(1) amortised add/remove.

    Rows live in a growable float32 buffer (numpy) or a list (no numpy).
    Removing a row swaps the last row into its slot, so updates never copy
    the whole matrix; ``meta[i]`` always describes row ``i``.
    """

    def __init__(self) -> None:
        self.meta: list[dict[str, Any]] = []
        self.dim: int | None = None
        self._buf = None  # numpy: (capacity, dim) float32
        self._rows: list[list[float]] = []  # pure-Python fallback
        self._by_path: dict[str, set[int]] = {}

    def __len__(self) -> int:
        return len(self.meta)

    def add(self, meta: dict[str, Any], vec) -> bool:
        if self.dim is None:
            self.dim = len(vec)
        elif len(vec) != self.dim:
            logger.warning(
                "Embedding dim %d != index dim %d; skipping chunk of %s",
                len(vec),
                self.dim,
                meta.get("path"),
            )
            return False
        i = len(self.meta)
        if _np is not None:
            if self._buf is None or self._buf.shape[0] <= i:
                cap = max(64, 2 * (self._buf.shape[0] if self._buf is not None else 0))
                new = _np.empty((cap, self.dim), dtype=_np.float32)
                if self._buf is not None:
                    new[:i] = self._buf[:i]
                self._buf = new
            self._buf[i] = vec
        else:
            self._rows.append(list(vec))
        self.meta.append(meta)
        self._by_path.setdefault(meta["path"], set()).add(i)
        return True

    def _remove_row(self, i: int) -> None:
        last = len(self.meta) - 1
        path = self.meta[i]["path"]
        self._by_path[path].discard(i)
        if not self._by_path[path]:
            del self._by_path[path]
        if i != last:
            moved = self.meta[last]
            self.meta[i] = moved
            if _np is not None:
                self._buf[i] = self._buf[last]
            else:
                self._rows[i] = self._rows[last]
            rows = self._by_path[moved["path"]]
            rows.discard(last)
            rows.add(i)
        self.meta.pop()
        if _np is None:
            self._rows.pop()

    def remove_path(self, path: str) -> int:
        rows = sorted(self._by_path.get(path, ()), reverse=True)
        for i in rows:  # highest first, so swapped-in rows are never ones we still need
            self._remove_row(i)
        return len(rows)

    def has_path(self, path: str) -> bool:
        return path in self._by_path

    def clear(self) -> None:
        self.__init__()

    def scores(self, q):
        """Cosine similarity of every row with normalised query ``q``."""
        n = len(self.meta)
        if _np is not None:
            # einsum = one single-threaded SIMD pass.  BLAS gemv (``@``) spins up every
            # OpenBLAS thread and was 3-10x slower on a loaded multi-core box.
            return _np.einsum("ij,j->i", self._buf[:n], q)
        return [sum(a * b for a, b in zip(r, q, strict=False)) for r in self._rows]

    def to_bytes(self) -> bytes:
        n = len(self.meta)
        if n == 0:
            return b""
        if _np is not None:
            return self._buf[:n].astype("<f2").tobytes()
        flat = [x for r in self._rows for x in r]
        return struct.pack(f"<{len(flat)}e", *flat)

    def load(self, metas: list[dict[str, Any]], raw: bytes, dim: int) -> None:
        self.clear()
        n = len(metas)
        if len(raw) != n * dim * 2:
            raise ValueError(f"vector file has {len(raw)} bytes, expected {n * dim * 2}")
        if n == 0:
            return
        self.dim = dim
        if _np is not None:
            self._buf = _np.frombuffer(raw, dtype="<f2").reshape(n, dim).astype(_np.float32)
        else:
            flat = struct.unpack(f"<{n * dim}e", raw)
            self._rows = [list(flat[i * dim : (i + 1) * dim]) for i in range(n)]
        self.meta = list(metas)
        for i, m in enumerate(self.meta):
            self._by_path.setdefault(m["path"], set()).add(i)


@dataclass
class SearchResult:
    path: str
    content_excerpt: str
    score: float
    match_type: str  # "semantic", "keyword", "hybrid", "name"


class SemanticIndex:
    """
    Semantic search index for the vault.
    Uses local embeddings (sentence-transformers) for zero API cost.

    The index is resident in memory once loaded: queries are a single
    matrix-vector product, never a re-read of the index file.  On disk it is a
    float16 matrix plus a small JSON metadata file (``index_meta.json``).

    With a :class:`~ompa.keyword.KeywordIndex` attached (``keyword_index=``),
    ``search(hybrid=True)`` fuses semantic and BM25 rankings with reciprocal
    rank fusion.  Falls back to keyword search if embeddings are not available.
    """

    def __init__(
        self,
        index_path: Path,
        model_name: str = "all-MiniLM-L6-v2",
        embedding_dim: int = 384,
        embedding_backend: EmbeddingBackend | None = None,
        keyword_index: Any = None,
    ):
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)
        self.model_name = model_name
        self.embedding_dim = embedding_dim
        self.keyword_index = keyword_index
        self._store = _VectorStore()
        # path -> [st_mtime_ns, st_size] when the file was last indexed; drives refresh()
        self.file_mtimes: dict[str, list[int]] = {}
        self._dirty = False
        self._initialized = False
        self._lock = threading.RLock()
        # Accept a pre-built backend (e.g. NIMEmbeddingBackend) or load lazily
        self._model: EmbeddingBackend | None = embedding_backend
        if embedding_backend is not None:
            self._initialized = True

    @property
    def chunks(self) -> list[dict[str, Any]]:
        """Metadata of every indexed chunk (path, chunk_index, hash, text excerpt)."""
        return self._store.meta

    @property
    def model(self):
        """Lazy-load the model on first access."""
        if self._model is None:
            self._init_model()
        return self._model

    @property
    def available(self) -> bool:
        """True if an embedding model is (or can be) loaded."""
        if not self._initialized:
            self._init_model()
        return self._model is not None

    def _init_model(self) -> None:
        """Initialize the embedding model (called lazily)."""
        if self._initialized:
            return
        try:
            self._model = _load_sentence_transformer(self.model_name)
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

    # ---------------------------------------------------------------- encoding

    def _encode_many(self, texts: list[str]) -> list:
        model = self.model
        if not texts:
            return []
        if hasattr(model, "encode_batch"):
            return list(model.encode_batch(texts))
        if type(model).__module__.startswith("sentence_transformers"):
            return list(model.encode(texts, batch_size=32, show_progress_bar=False))
        return [model.encode(t) for t in texts]

    def _index_paths(self, paths: list[Path]) -> int:
        """(Re-)embed ``paths`` in batches; returns how many files were indexed."""
        pending: list[tuple[str, int, str]] = []
        sigs: dict[str, list[int]] = {}
        for path in paths:
            path_str = str(path)
            try:
                sig = _file_signature(path)
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError) as e:
                logger.warning("Error indexing %s: %s", path, e)
                continue
            sigs[path_str] = sig
            for i, text in _chunk_texts(content):
                pending.append((path_str, i, text))
        try:
            vectors = self._encode_many([t for _, _, t in pending])
        except Exception as e:
            # One bad text should not sink the batch: fall back to one at a time.
            logger.debug("Batch encode failed (%s); encoding chunk by chunk", e)
            vectors = []
            for _, _, t in pending:
                try:
                    vectors.append(self.model.encode(t))
                except Exception as e2:
                    logger.warning("Error embedding chunk: %s", e2)
                    vectors.append(None)
        with self._lock:
            for path_str in sigs:
                self._store.remove_path(path_str)
            for (path_str, i, text), vec in zip(pending, vectors, strict=True):
                if vec is None:
                    continue
                try:
                    v = _normalized(vec)
                except Exception as e:
                    logger.warning("Error indexing %s: %s", path_str, e)
                    continue
                meta = {
                    "hash": hashlib.sha256(f"{path_str}:{i}".encode()).hexdigest()[:16],
                    "path": path_str,
                    "chunk_index": i,
                    "text": _excerpt(text),
                }
                self._store.add(meta, v)
            self.file_mtimes.update(sigs)
            if sigs:
                self._dirty = True
        return len(sigs)

    def index_file(self, path: Path) -> None:
        """Index a single file (or re-index if already present)."""
        path = Path(path)
        if not path.exists():
            return
        if not self._initialized:
            self._init_model()
        if self._model is None:
            return
        try:
            self._index_paths([path])
        except Exception as e:
            logger.warning("Error indexing %s: %s", path, e)

    def update_file(self, path: Path, save: bool = True) -> bool:
        """
        Incrementally update the index for a single file.
        Re-indexes the file and (by default) saves the updated index.
        Pass ``save=False`` to defer persistence; call :meth:`flush` later.
        Returns True if successful.
        """
        path = Path(path)
        if not path.exists() or path.suffix != ".md":
            return False

        try:
            self.index_file(path)
            if save:
                self.save_index()
            logger.debug("Incrementally updated index for %s", path)
            return True
        except Exception as e:
            logger.warning("Incremental index update failed for %s: %s", path, e)
            return False

    def remove_file(self, path: Path) -> bool:
        """Remove a file from the index (e.g., after deletion)."""
        path_str = str(Path(path))
        with self._lock:
            removed = self._store.remove_path(path_str)
            had_mtime = self.file_mtimes.pop(path_str, None) is not None
            if removed > 0 or had_mtime:
                self._dirty = True
        if removed > 0 or had_mtime:
            self.save_index()
            logger.debug("Removed %d chunks for %s", removed, path)
        return removed > 0

    def _vault_files(self, vault_path: Path, exclude_patterns: list | None) -> list[Path]:
        return [Path(p) for p in scan_vault(vault_path, exclude_patterns)]

    def index_vault(self, vault_path: Path, exclude_patterns: list = None) -> int:
        """Index all markdown files in a vault (embedding in batches)."""
        if not self._initialized:
            self._init_model()
        if self._model is None:
            return 0
        files = self._vault_files(vault_path, exclude_patterns)
        try:
            self._index_paths(files)
        except Exception as e:
            logger.warning("Error indexing vault %s: %s", vault_path, e)
        return len(files)

    def refresh(
        self,
        vault_path: Path,
        exclude_patterns: list | None = None,
        snapshot: dict[str, list[int]] | None = None,
    ) -> dict:
        """Bring the index up to date with the vault without a full rebuild.

        Re-embeds only notes that are new or whose ``[mtime_ns, size]`` changed
        since they were indexed, and drops notes that no longer exist.  Saves the
        index when anything changed.  ``snapshot`` is an optional precomputed
        :func:`scan_vault` result.  Returns ``{"added": n, "updated": n, "removed": n}``.
        """
        stats = {"added": 0, "updated": 0, "removed": 0}
        if not self._initialized:
            self._init_model()
        if self._model is None:
            return stats

        vault_path = Path(vault_path)
        if snapshot is None:
            snapshot = scan_vault(vault_path, exclude_patterns)
        changed: list[Path] = []
        for path_str, sig in snapshot.items():
            known = self.file_mtimes.get(path_str)
            if known == sig:
                continue
            changed.append(Path(path_str))
            stats["updated" if known is not None else "added"] += 1
        if changed:
            self._index_paths(changed)

        vault_prefix = str(vault_path)
        with self._lock:
            for path_str in [
                p for p in self.file_mtimes if p.startswith(vault_prefix) and p not in snapshot
            ]:
                self._store.remove_path(path_str)
                del self.file_mtimes[path_str]
                self._dirty = True
                stats["removed"] += 1

        if any(stats.values()):
            self.save_index()
        return stats

    # ------------------------------------------------------------- persistence

    def flush(self) -> None:
        """Persist the index if there are unsaved incremental updates."""
        if self._dirty:
            self.save_index()

    def save_index(self) -> None:
        """Save the index to disk (float16 vectors + JSON metadata, atomically)."""
        with self._lock:
            meta = {
                "version": INDEX_FORMAT_VERSION,
                "model": self.model_name,
                "dim": self._store.dim or 0,
                "count": len(self._store),
                "chunks": self._store.meta,
                "files": self.file_mtimes,
            }
            raw = self._store.to_bytes()
            # vectors first, metadata last: load_index() cross-checks the two sizes
            _atomic_write(self.index_path / VECTORS_FILE, raw)
            _atomic_write(
                self.index_path / META_FILE,
                json.dumps(meta, separators=(",", ":")).encode("utf-8"),
            )
            self._dirty = False
        legacy = self.index_path / LEGACY_FILE
        if legacy.exists():
            try:
                legacy.unlink()  # superseded by the v2 files just written
            except OSError as e:
                logger.debug("Could not remove legacy index %s: %s", legacy, e)

    def load_index(self) -> bool:
        """Load the index from disk into memory (v2 format, or migrate a v1 JSON index)."""
        meta_file = self.index_path / META_FILE
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                raw = (self.index_path / VECTORS_FILE).read_bytes()
                with self._lock:
                    self._store.load(meta["chunks"], raw, int(meta.get("dim") or 0))
                    self.file_mtimes = {
                        str(k): [int(x) for x in v] for k, v in meta.get("files", {}).items()
                    }
                    self._dirty = False
                return True
            except Exception as e:
                logger.warning("Error loading index (will rebuild): %s", e)
                with self._lock:
                    self._store.clear()
                    self.file_mtimes = {}
                return False
        return self._load_legacy()

    def _load_legacy(self) -> bool:
        """Read a v1 ``semantic_index.json`` (OMPA <= 1.0.8) and convert it in memory."""
        index_file = self.index_path / LEGACY_FILE
        if not index_file.exists():
            return False

        try:
            with open(index_file, encoding="utf-8") as f:
                data = json.load(f)

            chunks = data["chunks"]
            files = data.get("files")
            if files is None:
                # Index written before mtime tracking: treat every indexed note as
                # current as of the index file's own mtime, so notes edited (or
                # added) after the last save are picked up by refresh().
                saved_at = index_file.stat().st_mtime_ns
                files = {}
                for c in chunks:
                    try:
                        sig = _file_signature(Path(c["path"]))
                    except OSError:
                        sig = [saved_at, -1]
                    # unchanged since the save -> current; newer -> forces re-index
                    files[c["path"]] = sig if sig[0] <= saved_at else [saved_at, -1]
            with self._lock:
                self._store.clear()
                for c in chunks:
                    meta = {
                        "hash": c.get("hash", ""),
                        "path": c["path"],
                        "chunk_index": c.get("chunk_index", 0),
                        "text": _excerpt(c.get("text", "")),
                    }
                    self._store.add(meta, _normalized(c["embedding"]))
                self.file_mtimes = {
                    str(k): [int(x) for x in v] if isinstance(v, list) else [int(v), -1]
                    for k, v in files.items()
                }
                self._dirty = True
            try:  # migrate now: write the v2 files (and drop the v1 JSON) once
                self.save_index()
            except OSError as e:
                logger.debug("Legacy index migration deferred: %s", e)
            return True
        except Exception as e:
            logger.warning("Error loading index: %s", e)
            return False

    # ------------------------------------------------------------------ search

    def _semantic_ranked(self, query: str, limit: int) -> list[SearchResult]:
        """Top ``limit`` notes by best-chunk cosine similarity (one result per note)."""
        q = _normalized(self.model.encode(query))
        with self._lock:
            n = len(self._store)
            if n == 0:
                return []
            if self._store.dim is not None and len(q) != self._store.dim:
                raise ValueError(f"query dim {len(q)} != index dim {self._store.dim}")
            scores = self._store.scores(q)
            metas = self._store.meta
            out: list[SearchResult] = []
            seen: set[str] = set()
            k = min(n, max(4 * limit, limit + 16))
            while True:
                if _np is not None:
                    if k < n:
                        top = _np.argpartition(-scores, k - 1)[:k]
                        order = top[_np.argsort(-scores[top], kind="stable")]
                    else:
                        order = _np.argsort(-scores, kind="stable")
                else:
                    order = sorted(range(n), key=lambda i: -scores[i])[:k]
                out, seen = [], set()
                for i in order:
                    m = metas[int(i)]
                    if m["path"] in seen:
                        continue
                    seen.add(m["path"])
                    out.append(
                        SearchResult(
                            path=m["path"],
                            content_excerpt=m["text"],
                            score=float(scores[int(i)]),
                            match_type="semantic",
                        )
                    )
                    if len(out) >= limit:
                        return out
                if k >= n:
                    return out
                k = min(n, k * 4)  # many chunks per note: widen and retry

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
            hybrid: fuse semantic + BM25 keyword rankings (reciprocal rank fusion)
                if a keyword index is attached; semantic only if False

        Returns:
            List of SearchResult, best first
        """
        limit = max(0, min(limit, 100))  # Cap to prevent DoS
        if limit == 0:
            return []

        if not self._initialized or self._model is None or not self.chunks:
            return self._keyword_search(query, limit)

        try:
            if not (hybrid and self.keyword_index is not None):
                return self._semantic_ranked(query, limit)
            depth = max(limit, 20)
            return fuse_rrf(
                self._semantic_ranked(query, depth),
                self.keyword_index.search(query, depth),
                limit,
            )
        except Exception as e:
            logger.warning("Search error: %s", e)
            return self._keyword_search(query, limit)

    def _keyword_search(self, query: str, limit: int) -> list[SearchResult]:
        """Keyword search: BM25 via the attached FTS5 index, else a substring scan."""
        if self.keyword_index is not None and getattr(self.keyword_index, "available", False):
            return self.keyword_index.search(query, limit)

        query_lower = query.lower()
        results = []

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
        with self._lock:
            self._store.clear()
            self.file_mtimes = {}
            self._dirty = False
        for name in (META_FILE, VECTORS_FILE, LEGACY_FILE):
            f = self.index_path / name
            if f.exists():
                f.unlink()


def fuse_rrf(
    semantic: list[SearchResult], keyword: list[SearchResult], limit: int, k: int = RRF_K
) -> list[SearchResult]:
    """Reciprocal rank fusion of two best-first result lists (one entry per path)."""
    fused: dict[str, SearchResult] = {}
    scores: dict[str, float] = {}
    for results, kind in ((semantic, "semantic"), (keyword, "keyword")):
        for rank, r in enumerate(results):
            scores[r.path] = scores.get(r.path, 0.0) + 1.0 / (k + rank + 1)
            if r.path not in fused:
                fused[r.path] = SearchResult(r.path, r.content_excerpt, 0.0, kind)
            elif fused[r.path].match_type != kind:
                fused[r.path].match_type = "hybrid"
    for path, s in scores.items():
        fused[path].score = s
    return sorted(fused.values(), key=lambda r: r.score, reverse=True)[:limit]


def qmd_query(vault_path: str, query: str, limit: int = 5) -> list[SearchResult]:
    """
    Convenience function for QMD-style queries.
    Mimics the qmd CLI behavior.
    """
    from .keyword import KeywordIndex

    palace = Path(vault_path) / ".palace"
    keyword = KeywordIndex(palace / "keyword_index.sqlite3")
    keyword.refresh(Path(vault_path))
    index = SemanticIndex(palace / "semantic_index", keyword_index=keyword)
    index.load_index()
    return index.search(query, limit)
