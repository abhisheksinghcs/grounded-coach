"""Azure AI Search retrieval adapter (keyword-first), independent of audio.

Design choices:
  * Talks to the Search REST API with ``httpx`` so it is trivially mockable and
    matches the rest of the app's async style.
  * Field names are **configurable mappings** (``AZURE_SEARCH_*_FIELD``) so the
    same code works against any index schema.
  * Keyword search is the default. Hybrid (integrated vectorization) is behind
    ``AZURE_SEARCH_USE_HYBRID`` and only works if the index has a vectorizer +
    vector field; it is OFF until that setup is confirmed.
  * Empty retrieval returns an empty list (never a fabricated answer).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from coach.config import Settings


class RetrievalError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(slots=True)
class RetrievedDoc:
    """A normalized search hit, decoupled from the index's field names."""

    source_id: str
    title: str
    content: str
    url: str
    score: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "title": self.title,
            "content": self.content,
            "url": self.url,
            "score": self.score,
        }


class SearchTokenProvider:
    """api-key header, or an Entra token for the Search data plane."""

    _SCOPE = "https://search.azure.com/.default"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._credential = None

    def auth_headers(self) -> dict[str, str]:
        if self._settings.azure_search_api_key:
            return {"api-key": self._settings.azure_search_api_key}
        if self._credential is None:
            from azure.identity import DefaultAzureCredential

            self._credential = DefaultAzureCredential()
        token = self._credential.get_token(self._SCOPE)
        return {"Authorization": f"Bearer {token.token}"}


class SearchAdapter:
    """Executes retrieval against an Azure AI Search index."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient,
        token_provider: SearchTokenProvider | None = None,
    ) -> None:
        self._settings = settings
        self._client = client
        self._tokens = token_provider or SearchTokenProvider(settings)

    @property
    def search_url(self) -> str:
        s = self._settings
        return (
            f"{s.azure_search_endpoint}/indexes/{s.azure_search_index}/docs/search"
            f"?api-version={s.azure_search_api_version}"
        )

    def _build_body(self, query: str) -> dict[str, Any]:
        s = self._settings
        body: dict[str, Any] = {
            "search": query,
            "top": s.azure_search_top,
            "queryType": "simple",  # keyword-first
        }
        fields = s.search_fields_list
        if fields:
            body["searchFields"] = ",".join(fields)
        # Hybrid (integrated vectorization) only when explicitly enabled AND a
        # vector field is configured. Relies on an index-side vectorizer.
        if s.azure_search_use_hybrid and s.azure_search_vector_field:
            body["vectorQueries"] = [
                {
                    "kind": "text",
                    "text": query,
                    "fields": s.azure_search_vector_field,
                }
            ]
        return body

    async def search(self, query: str) -> list[RetrievedDoc]:
        s = self._settings
        if not s.azure_search_index:
            raise RetrievalError(
                "No Azure AI Search index configured (AZURE_SEARCH_INDEX is empty)."
            )
        if not query or not query.strip():
            return []

        headers = {"Content-Type": "application/json"}
        headers.update(self._tokens.auth_headers())
        try:
            resp = await self._client.post(
                self.search_url, json=self._build_body(query), headers=headers
            )
        except httpx.HTTPError as exc:
            raise RetrievalError(f"Failed to reach Azure AI Search: {exc}") from exc

        if resp.status_code in (401, 403):
            raise RetrievalError(
                "Authentication to Azure AI Search failed.",
                status_code=resp.status_code,
            )
        if resp.status_code >= 400:
            raise RetrievalError(
                f"Azure AI Search returned {resp.status_code}: {resp.text[:300]}",
                status_code=resp.status_code,
            )

        data = resp.json()
        hits = data.get("value", []) or []
        return [self._map(doc) for doc in hits]

    def _map(self, doc: dict[str, Any]) -> RetrievedDoc:
        """Map an index document onto the normalized shape via field mappings."""
        s = self._settings
        return RetrievedDoc(
            source_id=str(_first(doc, s.azure_search_id_field, default="")),
            title=str(_first(doc, s.azure_search_title_field, default="")),
            content=str(_first(doc, s.azure_search_content_field, default="")),
            url=str(_first(doc, s.azure_search_url_field, default="")),
            score=float(doc.get("@search.score", 0.0) or 0.0),
        )


def _first(doc: dict[str, Any], field: str, *, default: Any) -> Any:
    val = doc.get(field, default)
    return default if val is None else val
