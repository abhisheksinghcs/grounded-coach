"""Milestone 3: Azure AI Search retrieval adapter (independent of audio).

Covers: field mapping, EMPTY SEARCH, failed authentication, keyword request
shape, hybrid toggle, and the standalone /api/search endpoint.
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from coach.app import create_app
from coach.config import Settings
from coach.retrieval.search_adapter import (
    RetrievalError,
    SearchAdapter,
    SearchTokenProvider,
)

SEARCH_URL = (
    "https://secondchat.search.windows.net/indexes/coach-index/docs/search"
    "?api-version=2024-07-01"
)


def _adapter(settings: Settings, client: httpx.AsyncClient) -> SearchAdapter:
    return SearchAdapter(
        settings, client=client, token_provider=SearchTokenProvider(settings)
    )


@respx.mock
async def test_search_maps_fields_and_score(settings):
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "id": "doc-1",
                        "title": "Negotiation basics",
                        "content": "Anchor high, concede slowly.",
                        "url": "https://kb/doc-1",
                        "@search.score": 4.2,
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        docs = await _adapter(settings, client).search("how to negotiate")
    assert len(docs) == 1
    d = docs[0]
    assert d.source_id == "doc-1"
    assert d.title == "Negotiation basics"
    assert d.content.startswith("Anchor high")
    assert d.url == "https://kb/doc-1"
    assert d.score == pytest.approx(4.2)


@respx.mock
async def test_custom_field_mappings(settings):
    settings = settings.model_copy(
        update={
            "azure_search_id_field": "chunk_id",
            "azure_search_content_field": "text",
            "azure_search_title_field": "name",
            "azure_search_url_field": "source",
        }
    )
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {
                        "chunk_id": "c7",
                        "name": "Title7",
                        "text": "Body7",
                        "source": "https://x/7",
                        "@search.score": 1.0,
                    }
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        docs = await _adapter(settings, client).search("q")
    assert docs[0].source_id == "c7"
    assert docs[0].title == "Title7"
    assert docs[0].content == "Body7"
    assert docs[0].url == "https://x/7"


@respx.mock
async def test_empty_search_returns_empty_list(settings):
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(200, json={"value": []}))
    async with httpx.AsyncClient() as client:
        docs = await _adapter(settings, client).search("nothing matches")
    assert docs == []


async def test_blank_query_short_circuits(settings):
    async with httpx.AsyncClient() as client:
        docs = await _adapter(settings, client).search("   ")
    assert docs == []


async def test_missing_index_raises(settings):
    settings = settings.model_copy(update={"azure_search_index": ""})
    async with httpx.AsyncClient() as client:
        with pytest.raises(RetrievalError):
            await _adapter(settings, client).search("q")


@respx.mock
async def test_failed_authentication_raises(settings):
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(403, text="forbidden"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(RetrievalError) as exc:
            await _adapter(settings, client).search("q")
    assert exc.value.status_code == 403


@respx.mock
async def test_keyword_request_shape(settings):
    settings = settings.model_copy(
        update={"azure_search_search_fields": "content,title", "azure_search_top": 5}
    )
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    async with httpx.AsyncClient() as client:
        await _adapter(settings, client).search("hello")
    body = route.calls.last.request.read()
    import json

    sent = json.loads(body)
    assert sent["search"] == "hello"
    assert sent["queryType"] == "simple"
    assert sent["top"] == 5
    assert sent["searchFields"] == "content,title"
    assert "vectorQueries" not in sent  # hybrid off by default


@respx.mock
async def test_hybrid_toggle_adds_vector_query(settings):
    settings = settings.model_copy(
        update={
            "azure_search_use_hybrid": True,
            "azure_search_vector_field": "contentVector",
        }
    )
    route = respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(200, json={"value": []})
    )
    async with httpx.AsyncClient() as client:
        await _adapter(settings, client).search("hello")
    import json

    sent = json.loads(route.calls.last.request.read())
    assert sent["vectorQueries"][0]["fields"] == "contentVector"
    assert sent["vectorQueries"][0]["kind"] == "text"


@respx.mock
def test_api_search_endpoint(settings):
    respx.post(SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "value": [
                    {"id": "d1", "title": "T", "content": "C", "url": "u",
                     "@search.score": 2.0}
                ]
            },
        )
    )
    with TestClient(create_app(settings)) as client:
        resp = client.post("/api/search", json={"query": "anything"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 1
        assert body["results"][0]["source_id"] == "d1"


@respx.mock
def test_api_search_endpoint_auth_failure(settings):
    respx.post(SEARCH_URL).mock(return_value=httpx.Response(401, text="no"))
    with TestClient(create_app(settings)) as client:
        resp = client.post("/api/search", json={"query": "x"})
        assert resp.status_code == 401
