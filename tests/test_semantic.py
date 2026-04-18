"""Tests for semantic."""

import tempfile
from pathlib import Path


class TestSemanticIndex:
    """Test semantic index behavior."""

    def test_index_vault_initializes_model(self):
        """index_vault should lazy-init the model if not yet initialized."""
        from pathlib import Path

        from ompa.semantic import SemanticIndex

        with tempfile.TemporaryDirectory() as tmpdir:
            idx = SemanticIndex(index_path=Path(tmpdir) / "idx")
            assert idx._initialized is False

            # Monkeypatch _init_model to track that it was called
            called = []

            def fake_init():
                called.append(True)
                idx._initialized = True
                idx._model = "fake"

            idx._init_model = fake_init

            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            (vault / "test.md").write_text("Hello world content here", encoding="utf-8")

            count = idx.index_vault(vault)
            assert len(called) == 1  # _init_model was triggered
            assert count >= 1

    # ------------------------------------------------------------------
    # P1.2 — v2 binary storage format (chunks meta + .npy embedding matrix)
    # ------------------------------------------------------------------

    def _make_stub_index(self, tmpdir):
        """Helper: build a SemanticIndex with a deterministic stub model that
        avoids downloading sentence-transformers during tests.

        The stub encodes each input string to a tiny 4-dim vector based on
        character counts — enough for meaningful cosine similarity without
        any external model dependency.
        """
        from pathlib import Path

        import numpy as np

        from ompa.semantic import SemanticIndex

        class StubModel:
            def encode(self, texts, convert_to_numpy=True, show_progress_bar=False):
                # Accept a string or a list of strings.
                single = isinstance(texts, str)
                items = [texts] if single else list(texts)
                out = np.zeros((len(items), 4), dtype=np.float32)
                for i, t in enumerate(items):
                    tl = t.lower()
                    out[i, 0] = tl.count("a") + 0.1
                    out[i, 1] = tl.count("e") + 0.1
                    out[i, 2] = tl.count("o") + 0.1
                    out[i, 3] = len(tl) % 7 + 0.1
                if single:
                    return out[0]
                return out

        idx = SemanticIndex(
            index_path=Path(tmpdir) / "idx",
            embedding_dim=4,
        )
        idx._model = StubModel()
        idx._initialized = True
        return idx

    def test_v2_binary_format_roundtrip(self):
        """After index_file + save_index, we should see meta.json + .npy on disk,
        and load_index should rebuild the in-memory state identically."""
        import numpy as np

        with tempfile.TemporaryDirectory() as tmpdir:
            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            note = vault / "notes.md"
            note.write_text(
                "Alpha beta gamma " * 30 + "\n\n" + "delta epsilon zeta " * 30,
                encoding="utf-8",
            )

            idx = self._make_stub_index(tmpdir)
            idx.index_file(note)
            idx.save_index()

            meta_file = idx.index_path / "semantic_index.meta.json"
            emb_file = idx.index_path / "semantic_index.embeddings.npy"
            legacy_file = idx.index_path / "semantic_index.json"

            assert meta_file.exists(), "v2 meta file should be written"
            assert emb_file.exists(), "v2 embedding matrix should be written"
            assert not legacy_file.exists(), "legacy JSON file should not exist"

            original_chunks = list(idx.chunks)
            original_emb = np.asarray(idx.embeddings, dtype=np.float32).copy()
            assert original_emb.ndim == 2
            assert original_emb.shape[0] == len(original_chunks)

            # Reload into a fresh index and compare.
            fresh = self._make_stub_index(tmpdir)
            assert fresh.load_index() is True
            assert fresh.chunks == original_chunks
            np.testing.assert_allclose(
                np.asarray(fresh.embeddings, dtype=np.float32),
                original_emb,
                rtol=1e-6,
                atol=1e-6,
            )

    def test_legacy_json_format_is_migrated_on_load(self):
        """Legacy single-JSON indexes (per-chunk embedding lists) must be
        auto-migrated to the v2 split format on first load, and the old
        file deleted so subsequent loads are fast."""
        import json as _json

        import numpy as np

        with tempfile.TemporaryDirectory() as tmpdir:
            idx = self._make_stub_index(tmpdir)
            idx.index_path.mkdir(parents=True, exist_ok=True)

            legacy_chunks = [
                {
                    "hash": "abc123",
                    "path": str(Path(tmpdir) / "a.md"),
                    "chunk_index": 0,
                    "text": "hello world from a",
                    "embedding": [0.1, 0.2, 0.3, 0.4],
                },
                {
                    "hash": "def456",
                    "path": str(Path(tmpdir) / "b.md"),
                    "chunk_index": 0,
                    "text": "another chunk from b",
                    "embedding": [0.5, 0.6, 0.7, 0.8],
                },
            ]
            legacy_path = idx.index_path / "semantic_index.json"
            legacy_path.write_text(
                _json.dumps(
                    {"model": "stub", "embedding_dim": 4, "chunks": legacy_chunks}
                ),
                encoding="utf-8",
            )

            assert idx.load_index() is True
            # Metadata stripped of per-chunk `embedding` field.
            assert all("embedding" not in c for c in idx.chunks)
            assert [c["hash"] for c in idx.chunks] == ["abc123", "def456"]
            # Embeddings now live in a numpy matrix. v3 normalizes rows
            # at migration time so queries skip the per-row norm divide —
            # compare the L2-normalized originals, not the raw values.
            emb = np.asarray(idx.embeddings, dtype=np.float32)
            assert emb.shape == (2, 4)
            raw = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
            np.testing.assert_allclose(emb[0], raw / np.linalg.norm(raw), atol=1e-6)
            # Every row is unit norm after migration.
            np.testing.assert_allclose(
                np.linalg.norm(emb, axis=1),
                np.ones(2, dtype=np.float32),
                atol=1e-6,
            )
            # Legacy file deleted, v2 files written.
            assert not legacy_path.exists()
            assert (idx.index_path / "semantic_index.meta.json").exists()
            assert (idx.index_path / "semantic_index.embeddings.npy").exists()

    def test_index_file_uses_batched_encode(self):
        """A multi-chunk file should trigger a SINGLE batched encode call,
        not one encode per chunk (P1.2 goal)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = self._make_stub_index(tmpdir)

            call_count = {"n": 0, "batch_sizes": []}
            original_encode = idx._model.encode

            def spy_encode(texts, **kwargs):
                call_count["n"] += 1
                if isinstance(texts, list):
                    call_count["batch_sizes"].append(len(texts))
                else:
                    call_count["batch_sizes"].append(1)
                return original_encode(texts, **kwargs)

            idx._model.encode = spy_encode

            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            # Force 3+ chunks by making a long note (chunk_size = 512 words).
            big_note = vault / "big.md"
            big_note.write_text(
                ("word " * 600) + "\n" + ("other " * 600) + "\n" + ("more " * 600),
                encoding="utf-8",
            )
            idx.index_file(big_note)

            assert call_count["n"] == 1, (
                f"Expected 1 batched encode call for the whole file, got "
                f"{call_count['n']} (batches: {call_count['batch_sizes']})"
            )
            # All chunks were encoded together.
            assert call_count["batch_sizes"][0] >= 2

    # ------------------------------------------------------------------
    # P1.3 — best-per-path dedup (up to `limit` distinct files)
    # ------------------------------------------------------------------

    def test_search_returns_up_to_limit_distinct_paths(self):
        """If a single file contributes many top-scoring chunks, the search
        must still surface other files up to `limit`, not be dominated by
        one path (prior bug: sort-then-dedup could return <limit results)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            idx = self._make_stub_index(tmpdir)

            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            # File A — 3 chunks, each long enough to survive the 20-char filter.
            # Its chunks will happen to score high because they contain the
            # query token "alpha".
            (vault / "a.md").write_text(
                ("alpha " * 520) + "\n" + ("alpha " * 520) + "\n" + ("alpha " * 520),
                encoding="utf-8",
            )
            (vault / "b.md").write_text("alpha " * 100, encoding="utf-8")
            (vault / "c.md").write_text("alpha " * 100, encoding="utf-8")

            idx.index_file(vault / "a.md")
            idx.index_file(vault / "b.md")
            idx.index_file(vault / "c.md")

            results = idx.search("alpha", limit=3, hybrid=True)
            paths = [r.path for r in results]

            # Each result must be a distinct path.
            assert len(paths) == len(
                set(paths)
            ), f"Expected distinct paths per result, got: {paths}"
            # We should see all three files (not just a.md repeated).
            assert len(results) == 3
            assert {Path(p).name for p in paths} == {"a.md", "b.md", "c.md"}

    def test_remove_file_rebuilds_embedding_matrix(self):
        """Removing a file should drop exactly its rows from the embedding
        matrix, leaving the remaining rows row-aligned with self.chunks."""
        import numpy as np

        with tempfile.TemporaryDirectory() as tmpdir:
            idx = self._make_stub_index(tmpdir)

            vault = Path(tmpdir) / "vault"
            vault.mkdir()
            a = vault / "a.md"
            b = vault / "b.md"
            a.write_text("alpha beta gamma " * 30, encoding="utf-8")
            b.write_text("delta epsilon zeta " * 30, encoding="utf-8")

            idx.index_file(a)
            idx.index_file(b)
            before_total = len(idx.chunks)
            before_b = sum(1 for c in idx.chunks if c["path"] == str(b))
            assert before_b >= 1

            assert idx.remove_file(a) is True

            assert all(c["path"] != str(a) for c in idx.chunks)
            assert len(idx.chunks) == before_total - (before_total - before_b)
            # Row-alignment invariant.
            emb = np.asarray(idx.embeddings, dtype=np.float32)
            assert emb.shape[0] == len(idx.chunks)


