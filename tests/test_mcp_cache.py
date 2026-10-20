"""Tests for the MCP server's per-vault Ompa instance cache and OMPA_VAULT_PATH default."""

import json
import os
import shutil

import pytest

from ompa import mcp_server
from ompa.semantic import SemanticIndex


@pytest.fixture(autouse=True)
def _fresh_cache():
    mcp_server.clear_ompa_cache()
    yield
    mcp_server.clear_ompa_cache()


@pytest.fixture
def count_model_loads(monkeypatch):
    """Replace the sentence-transformers load with a counter (no model download)."""
    calls = {"n": 0}

    def fake_init(self):
        if self._initialized:
            return
        calls["n"] += 1
        self._model = None
        self._initialized = True

    monkeypatch.setattr(SemanticIndex, "_init_model", fake_init)
    return calls


def test_same_vault_reuses_instance(tmp_path, monkeypatch):
    built = []
    real = mcp_server.make_ompa
    monkeypatch.setattr(mcp_server, "make_ompa", lambda **kw: built.append(kw) or real(**kw))
    for _ in range(3):
        assert "error" not in mcp_server.handle_call_tool(
            "ao_status", {"vault_path": str(tmp_path)}
        )
    assert len(built) == 1


def test_relative_and_absolute_paths_share_entry(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    a = mcp_server.get_ompa(".")
    b = mcp_server.get_ompa(str(tmp_path))
    assert a is b


def test_semantic_and_plain_instances_are_separate(tmp_path):
    assert mcp_server.get_ompa(str(tmp_path), enable_semantic=True) is not mcp_server.get_ompa(
        str(tmp_path), enable_semantic=False
    )


def test_search_loads_embedding_model_once(tmp_path, count_model_loads):
    (tmp_path / "note.md").write_text("authentication decisions", encoding="utf-8")
    for _ in range(3):
        result = mcp_server.handle_call_tool(
            "ao_search", {"query": "authentication", "vault_path": str(tmp_path)}
        )
        assert "results" in result
    assert count_model_loads["n"] == 1


def test_external_index_change_is_reloaded(tmp_path, count_model_loads):
    ao = mcp_server.get_ompa(str(tmp_path), enable_semantic=True)
    sem = ao.semantic
    index_file = tmp_path / ".palace" / "semantic_index" / "semantic_index.json"
    chunk = {"path": "x.md", "text": "rebuilt elsewhere", "embedding": [0.0]}
    index_file.write_text(json.dumps({"model": "m", "chunks": [chunk]}), encoding="utf-8")
    os.utime(index_file, ns=(1, 1))  # force an mtime different from any recorded one

    again = mcp_server.get_ompa(str(tmp_path), enable_semantic=True)
    assert again is ao
    assert sem.chunks == [chunk]
    assert count_model_loads["n"] <= 1


def test_deleted_vault_gets_new_instance(tmp_path):
    vault = tmp_path / "v"
    vault.mkdir()
    first = mcp_server.get_ompa(str(vault))
    shutil.rmtree(vault)
    second = mcp_server.get_ompa(str(vault))
    assert second is not first


def test_cache_is_bounded(tmp_path):
    for i in range(mcp_server._CACHE_MAX_ENTRIES + 3):
        mcp_server.get_ompa(str(tmp_path / f"v{i}"))
    assert len(mcp_server._OMPA_CACHE) == mcp_server._CACHE_MAX_ENTRIES


def test_hook_clock_refreshed_per_call(tmp_path):
    ao = mcp_server.get_ompa(str(tmp_path))
    ao.hooks.timestamp = ao.hooks.timestamp.replace(year=2000)
    mcp_server.get_ompa(str(tmp_path))
    assert ao.hooks.timestamp.year != 2000


def test_writes_visible_to_later_reads(tmp_path):
    vp = str(tmp_path)
    mcp_server.handle_call_tool(
        "ao_kg_add",
        {"subject": "Kai", "predicate": "works_on", "object": "Orion", "vault_path": vp},
    )
    facts = mcp_server.handle_call_tool("ao_kg_query", {"entity": "Kai", "vault_path": vp})["facts"]
    assert [(f["subject"], f["object"]) for f in facts] == [("Kai", "Orion")]


def test_ompa_vault_path_env_is_default(tmp_path, monkeypatch):
    monkeypatch.setenv("OMPA_VAULT_PATH", str(tmp_path))
    mcp_server.handle_call_tool("ao_kg_add", {"subject": "A", "predicate": "p", "object": "B"})
    assert (tmp_path / ".palace" / "knowledge_graph.sqlite3").exists()
    # An explicit vault_path still wins.
    other = tmp_path / "other"
    mcp_server.handle_call_tool(
        "ao_kg_add", {"subject": "A", "predicate": "p", "object": "B", "vault_path": str(other)}
    )
    assert (other / ".palace" / "knowledge_graph.sqlite3").exists()


def test_env_vault_path_still_validated(monkeypatch):
    monkeypatch.setenv("OMPA_VAULT_PATH", "/")
    assert mcp_server.handle_call_tool("ao_status", {}) == {"error": "Invalid vault_path"}
