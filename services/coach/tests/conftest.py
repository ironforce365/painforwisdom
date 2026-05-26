"""Shared pytest fixtures for the coach test suite."""
from __future__ import annotations
import os
from pathlib import Path
import pytest

FIXTURE_VAULT = Path(__file__).parent / "fixtures" / "vault"


@pytest.fixture
def fixture_vault_dir() -> Path:
    """Path to the miniature 5-doc test vault."""
    assert FIXTURE_VAULT.exists(), f"missing fixture vault at {FIXTURE_VAULT}"
    return FIXTURE_VAULT


@pytest.fixture
def tmp_index_dir(tmp_path: Path) -> Path:
    d = tmp_path / "vault_rag"
    d.mkdir()
    return d


@pytest.fixture(autouse=True)
def _unset_anthropic_api_key(monkeypatch):
    """Defensive: never let a stray ANTHROPIC_API_KEY shadow OAuth in tests."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
