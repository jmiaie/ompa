"""Shared test configuration."""

import tempfile

import pytest


@pytest.fixture(autouse=True)
def _allow_temp_vaults(monkeypatch):
    """Let MCP tool calls open vaults under the system temp directory.

    The MCP server only opens its default vault and directories under
    ``$OMPA_VAULT_ROOTS``.  Tests create vaults in temp directories and pass
    them as ``vault_path``, so the temp directory is listed as an allowed root.
    Tests of the restriction itself remove the variable again.
    """
    monkeypatch.setenv("OMPA_VAULT_ROOTS", tempfile.gettempdir())
