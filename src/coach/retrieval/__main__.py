"""Standalone retrieval CLI (no audio): ``python -m coach.retrieval "query"``.

Useful for validating index configuration and field mappings independently of
the realtime/audio pipeline.
"""

from __future__ import annotations

import asyncio
import sys

import httpx

from coach.config import get_settings
from coach.retrieval.search_adapter import RetrievalError, SearchAdapter


async def _run(query: str) -> int:
    settings = get_settings()
    async with httpx.AsyncClient(timeout=20.0) as client:
        adapter = SearchAdapter(settings, client=client)
        try:
            docs = await adapter.search(query)
        except RetrievalError as exc:
            print(f"Retrieval error: {exc}", file=sys.stderr)
            return 2
    if not docs:
        print("No results.")
        return 0
    for d in docs:
        print(f"[{d.source_id}] ({d.score:.3f}) {d.title}")
        snippet = d.content[:200].replace("\n", " ")
        print(f"    {snippet}")
    return 0


def main() -> None:
    if len(sys.argv) < 2:
        print('Usage: python -m coach.retrieval "your query"', file=sys.stderr)
        raise SystemExit(1)
    raise SystemExit(asyncio.run(_run(" ".join(sys.argv[1:]))))


if __name__ == "__main__":
    main()
