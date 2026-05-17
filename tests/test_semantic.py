"""Tests for ompa.semantic — SemanticIndex."""

import tempfile


class TestSemanticIndex:
    """Test semantic index behavior."""

    def test_index_vault_initializes_model(self, monkeypatch):
        """index_vault() should initialize the model before indexing."""
        from pathlib import Path

        from ompa.semantic import SemanticIndex

        class DummyEmbedding(list):
            def tolist(self):
                return list(self)

        class DummyModel:
            def encode(self, _text):
                return DummyEmbedding([0.1, 0.2, 0.3])

        def fake_init(self):
            self._model = DummyModel()
            self._initialized = True

        monkeypatch.setattr(SemanticIndex, "_init_model", fake_init)

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir)
            note = vault_path / "brain" / "Semantic.md"
            note.parent.mkdir(parents=True, exist_ok=True)
            note.write_text(
                "This is a sufficiently long semantic indexing note with many words.",
                encoding="utf-8",
            )

            index = SemanticIndex(index_path=vault_path / ".palace" / "semantic_index")
            indexed = index.index_vault(vault_path)

            assert indexed >= 1
            assert len(index.chunks) >= 1

    def test_index_vault_lazy_init_called(self):
        """index_vault should lazy-init the model if not yet initialized."""
        from pathlib import Path

        from ompa.semantic import SemanticIndex

        with tempfile.TemporaryDirectory() as tmpdir:
            idx = SemanticIndex(index_path=Path(tmpdir) / "idx")
            assert idx._initialized is False

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
            assert len(called) == 1
            assert count >= 1
