"""Shared fixtures. Nothing here contacts Azure."""

from __future__ import annotations

import pytest

from coach.config import Settings


@pytest.fixture
def settings() -> Settings:
    """Deterministic settings that use an API key (no Entra/network)."""
    return Settings(
        _env_file=None,
        azure_openai_endpoint="https://shhchat.openai.azure.com",
        azure_openai_realtime_deployment="gpt-realtime-2.1",
        azure_openai_api_key="test-key-123",
        azure_search_endpoint="https://secondchat.search.windows.net",
        azure_search_index="coach-index",
        azure_search_api_key="search-key-123",
    )
