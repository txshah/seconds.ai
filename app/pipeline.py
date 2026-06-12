"""Ingestion cycles: acquire -> dedup -> enrich -> store -> log.

Two entry points share ONE persistence path, so leads look identical no matter
where they came from:

  run_ingest()    we pull Reddit RSS ourselves        (local-dev / backup source)
  ingest_items()  we accept already-scraped items      (Guild.ai + Firecrawl source)

Keeping enrichment + dedup + provenance here (not in the Guild sandbox) makes
the Python service the single writer of the `leads` contract.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone

from . import store
from .config import settings
from .db import bootstrap, get_client
from .enrich import enrich
from .scraper import scrape

_REDDIT_ID_RE = re.compile(r"/comments/([a-z0-9]+)", re.I)


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_dt(value) -> datetime:
    if not value:
        return _now()
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return _now()


def stable_id(source: str, source_url: str, native_id: str | None = None) -> str:
    """Source-agnostic lead id: reddit post id when we can parse it, else a
    stable hash of the URL (so open-web Firecrawl results dedup cleanly too)."""
    if native_id:
        return native_id
    if source == "reddit":
        m = _REDDIT_ID_RE.search(source_url or "")
        if m:
            return m.group(1)
    return hashlib.sha1((source_url or "").encode()).hexdigest()[:16]


def _item_to_post(item: dict) -> dict:
    source = item.get("source") or "web"
    url = item.get("source_url") or ""
    raw = item.get("raw")
    raw_json = raw if isinstance(raw, str) else json.dumps(raw or item, ensure_ascii=False)
    # Fill taxonomy from the reddit URL when the source didn't supply it
    # (Firecrawl search snippets carry the URL but not the subreddit field).
    subreddit = item.get("subreddit") or ""
    if not subreddit and source == "reddit":
        m = re.search(r"/r/([^/]+)", url)
        if m:
            subreddit = m.group(1)
    return {
        "post_id": stable_id(source, url, item.get("native_id")),
        "source": source,
        "subreddit": subreddit,
        "author": item.get("author") or "",
        "title": item.get("title") or "",
        "body": item.get("body") or "",
        "permalink": url,
        "external_url": item.get("external_url") or "",
        "created_utc": _parse_dt(item.get("created_utc")),
        "score": int(item.get("score") or 0),
        "num_comments": int(item.get("num_comments") or 0),
        "raw_json": raw_json[:60000],
    }


# --------------------------------------------------------------------------- #
# shared persistence
# --------------------------------------------------------------------------- #
def _dedup(client, posts: list[dict]) -> list[dict]:
    already = store.existing_post_ids(client, [p["post_id"] for p in posts])
    return [p for p in posts if p["post_id"] and p["post_id"] not in already]


def _persist(client, new_posts: list[dict], run_id: str) -> int:
    """Store raw posts + their enriched leads. Returns leads_created."""
    store.insert_raw_posts(client, new_posts, run_id)
    leads = []
    for p in new_posts:
        features = enrich(p)
        if features["signal_score"] < settings.signal_floor:
            continue
        leads.append({
            "lead_id": p["post_id"],
            "source": p["source"],
            "source_url": p["permalink"],
            "subreddit": p["subreddit"],
            "created_utc": p["created_utc"],
            "author": p["author"],
            "title": p["title"],
            "body": p["body"],
            **features,
        })
    store.insert_leads(client, leads, run_id)
    return len(leads)


def _run(client, source: str, query: str, acquire) -> dict:
    """Generic run wrapper: acquire() -> list[post dict], then dedup/persist/log."""
    run_id = uuid.uuid4().hex
    started = _now()
    status, error = "ok", ""
    fetched = new = leads = 0
    try:
        posts = acquire()
        fetched = len(posts)
        new_posts = _dedup(client, posts)
        new = len(new_posts)
        leads = _persist(client, new_posts, run_id)
    except Exception as exc:  # noqa: BLE001
        status, error = "error", f"{type(exc).__name__}: {exc}"

    finished = _now()
    store.log_run(client, {
        "run_id": run_id, "source": source, "query": query,
        "started_at": started, "finished_at": finished,
        "posts_fetched": fetched, "posts_new": new, "leads_created": leads,
        "status": status, "error": error,
    })
    return {
        "run_id": run_id, "status": status, "error": error or None,
        "posts_fetched": fetched, "posts_new": new, "leads_created": leads,
        "duration_s": round((finished - started).total_seconds(), 2),
    }


# --------------------------------------------------------------------------- #
# entry points
# --------------------------------------------------------------------------- #
def run_ingest(subreddits=None, listing=None, limit=None, ensure_schema=True) -> dict:
    """Pull Reddit RSS ourselves (local-dev / backup ingestion path)."""
    if ensure_schema:
        bootstrap()
    client = get_client()
    subs = subreddits or settings.subreddit_list
    query = f"{settings.reddit_listing if listing is None else listing}:{','.join(subs)}"
    return _run(client, "reddit",  query,
                lambda: scrape(subreddits=subs, listing=listing, limit=limit))


def ingest_items(items: list[dict], source_label: str = "firecrawl",
                 ensure_schema: bool = True) -> dict:
    """Accept already-scraped items (the Guild.ai + Firecrawl ingestion path)."""
    if ensure_schema:
        bootstrap()
    client = get_client()
    query = f"{source_label}:{len(items)} items"
    return _run(client, source_label, query,
                lambda: [_item_to_post(it) for it in items])


def run_firecrawl(queries=None, limit=None, ensure_schema: bool = True) -> dict:
    """Acquire via Firecrawl (Reddit + open web), then store through the one writer.

    This is the Python twin of the Guild agent — same source, same items, same
    `leads`. Lets us validate the Firecrawl path locally before deploying to Guild.
    """
    from .scraper import firecrawl

    if ensure_schema:
        bootstrap()
    client = get_client()

    def acquire():
        items = firecrawl.search(queries=queries, limit=limit)
        return [_item_to_post(it) for it in items]

    return _run(client, "firecrawl", "firecrawl:search", acquire)


if __name__ == "__main__":
    print(json.dumps(run_ingest(), indent=2))
