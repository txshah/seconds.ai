"""Firecrawl-backed acquisition: search Reddit + the open web for consumer-harm
signals.

This mirrors `guild/ingest-agent.ts` in Python, so the Guild+Firecrawl path is
testable locally (set FIRECRAWL_API_KEY) before the agent is ever deployed — and
it doubles as an alternate runner. It returns the same item shape `/leads/ingest`
accepts, so everything funnels through the one writer (enrich/dedup/store).
"""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from ..config import settings

FIRECRAWL_SEARCH_URL = "https://api.firecrawl.dev/v1/search"


def _derive_source(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return "reddit" if host == "reddit.com" or host.endswith(".reddit.com") else "web"


def map_results(payload: dict) -> list[dict]:
    """Map a Firecrawl /search response to /leads/ingest item dicts. Pure — unit-testable."""
    items: list[dict] = []
    for d in payload.get("data") or []:
        url = d.get("url") or ""
        if not url:
            continue
        meta = d.get("metadata") or {}
        body = d.get("markdown") or d.get("description") or meta.get("description") or ""
        items.append({
            "source": _derive_source(url),
            "source_url": url,
            "title": d.get("title") or meta.get("title") or "",
            "body": body[:8000],
        })
    return items


def search(queries: list[str] | None = None, limit: int | None = None,
           api_key: str | None = None, scrape: bool | None = None) -> list[dict]:
    api_key = api_key or settings.firecrawl_api_key
    if not api_key:
        raise RuntimeError("FIRECRAWL_API_KEY is not set — cannot run the Firecrawl source")
    queries = queries or settings.firecrawl_query_list
    limit = limit or settings.firecrawl_limit
    scrape = settings.firecrawl_scrape if scrape is None else scrape

    seen: set[str] = set()
    items: list[dict] = []
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    with httpx.Client(timeout=60) as client:
        for q in queries:
            try:
                body = {"query": q, "limit": limit}
                if scrape:  # full-page markdown — richer but minutes, not seconds
                    body["scrapeOptions"] = {"formats": ["markdown"]}
                resp = client.post(FIRECRAWL_SEARCH_URL, headers=headers, json=body)
                resp.raise_for_status()
                for it in map_results(resp.json()):
                    if it["source_url"] not in seen:
                        seen.add(it["source_url"])
                        items.append(it)
            except Exception as exc:  # noqa: BLE001 — one bad query never fails the run
                print(f"[firecrawl] query failed [{q}]: {exc}")
    return items
