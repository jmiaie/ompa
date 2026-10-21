"""Consistency checks for distribution metadata (server.json) and README benchmark claims."""

import json
import re
from pathlib import Path

import ompa
from ompa import mcp_server

ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    return re.search(r'^version = "([^"]+)"', text, re.M).group(1)


def _server_json() -> dict:
    return json.loads((ROOT / "server.json").read_text(encoding="utf-8"))


def test_server_json_versions_match_package():
    data = _server_json()
    version = _pyproject_version()
    assert data["version"] == version == ompa.__version__
    (pkg,) = data["packages"]
    assert pkg["version"] == version
    assert pkg["registryType"] == "pypi" and pkg["identifier"] == "ompa"
    assert any(a.get("value") == f"ompa=={version}" for a in pkg["runtimeArguments"])


def test_server_json_points_at_real_entry_point():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'ao-mcp = "ompa.mcp_server:main"' in pyproject
    (pkg,) = _server_json()["packages"]
    assert {"type": "positional", "value": "ao-mcp"} in pkg["runtimeArguments"]
    assert pkg["transport"] == {"type": "stdio"}


def test_server_json_env_vars_are_read_by_server(monkeypatch, tmp_path):
    (pkg,) = _server_json()["packages"]
    names = [e["name"] for e in pkg.get("environmentVariables", [])]
    assert names == ["OMPA_VAULT_PATH"]
    monkeypatch.setenv("OMPA_VAULT_PATH", str(tmp_path))
    assert mcp_server.default_vault_path() == str(tmp_path)


def test_registry_name_and_readme_ownership_marker():
    data = _server_json()
    assert data["name"] == "io.github.jmiaie/ompa"
    assert len(data["description"]) <= 100
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert f"mcp-name: {data['name']}" in readme


def test_longmemeval_number_is_attributed_not_claimed():
    """Every mention of 96.6% must sit next to MemPalace attribution (README fix)."""
    for rel in ("README.md", "docs/index.md", "docs/why-ompa.md"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        for para in re.split(r"\n\s*\n", text):
            for line in para.splitlines():
                if "96.6%" in line:
                    assert "MemPalace" in para, f"{rel}: unattributed 96.6% in {line!r}"
            assert "proven 96.6%" not in para
