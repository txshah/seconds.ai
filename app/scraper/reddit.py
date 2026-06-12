"""Reddit ingestion via the public Atom feeds (no OAuth required).

  GET https://www.reddit.com/r/<sub>/<listing>/.rss

As of 2026 Reddit blocks the legacy `/*.json` endpoints at the IP level (403
"Blocked") for non-OAuth clients, but the per-subreddit RSS/Atom feeds remain
open and return 200. They carry everything the pipeline needs: a stable t3_ id,
title, permalink (provenance), author, subreddit, publish time, and an HTML body.

Tradeoff vs the old JSON API: the feed omits score / num_comments and returns
~25 recent items per sub. Upgrade path: drop in a Reddit OAuth client
(REDDIT_CLIENT_ID / SECRET, 100 req/min, full fields) — `normalize()` returns
the same dict shape, so enrich / store / API / Pioneer are unaffected.
"""

from __future__ import annotations

import html
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from ..config import settings

REDDIT_BASE = "https://www.reddit.com"
ATOM = {"a": "http://www.w3.org/2005/Atom"}

_TAG_RE = re.compile(r"<[^>]+>")
_HREF_RE = re.compile(r'href="([^"]+)"')
_SUBMITTED_RE = re.compile(r"\s*submitted by\s+/u/\S+(?:\s+to\s+r/\S+)?", re.I)
_WS_RE = re.compile(r"\s+")

# RSS works with a browser-like UA; the descriptive UA is appended for honesty.
_BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
)


def _utc_naive(iso: str) -> datetime:
    """Parse an Atom ISO-8601 timestamp into a tz-naive UTC datetime."""
    try:
        dt = datetime.fromisoformat(iso)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc).replace(tzinfo=None)
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(tzinfo=None)


def _strip_html(content: str) -> str:
    text = html.unescape(content or "")
    text = _TAG_RE.sub(" ", text)
    text = _SUBMITTED_RE.sub("", text)
    text = text.replace("[link]", "").replace("[comments]", "")
    return _WS_RE.sub(" ", text).strip()


def _external_link(content: str) -> str:
    """First non-reddit absolute link in the post body, if any."""
    for raw in _HREF_RE.findall(content or ""):
        href = html.unescape(raw)
        if href.startswith("http") and "reddit.com" not in href:
            return href
    return ""


def _text(entry: ET.Element, path: str) -> str:
    el = entry.find(path, ATOM)
    return (el.text or "").strip() if el is not None and el.text else ""


def normalize(entry: ET.Element, requested_sub: str) -> dict:
    raw_id = _text(entry, "a:id")                       # e.g. "t3_1u43aa7"
    post_id = raw_id.split("_", 1)[-1] if "_" in raw_id else raw_id

    link_el = entry.find("a:link", ATOM)
    permalink = link_el.get("href", "") if link_el is not None else ""

    cat_el = entry.find("a:category", ATOM)
    subreddit = cat_el.get("term") if cat_el is not None and cat_el.get("term") else requested_sub

    author = _text(entry, "a:author/a:name").removeprefix("/u/")
    content_html = _text(entry, "a:content")

    return {
        "post_id": post_id,
        "source": "reddit",
        "subreddit": subreddit,
        "author": author,
        "title": _text(entry, "a:title"),
        "body": _strip_html(content_html),
        "permalink": permalink,
        "external_url": _external_link(content_html),
        "created_utc": _utc_naive(_text(entry, "a:published")),
        "score": 0,            # not exposed by RSS — restored by the OAuth upgrade
        "num_comments": 0,
        "raw_json": ET.tostring(entry, encoding="unicode")[:60000],
    }


def _fetch(sub: str, listing: str, limit: int, client: httpx.Client,
           max_retries: int = 3) -> list[dict]:
    url = f"{REDDIT_BASE}/r/{sub}/{listing}/.rss"
    headers = {"User-Agent": settings.reddit_user_agent or _BROWSER_UA}
    params = {"limit": min(max(limit, 1), 100)}

    for attempt in range(max_retries + 1):
        resp = client.get(url, params=params, headers=headers)
        if resp.status_code == 429 and attempt < max_retries:
            # Honor Retry-After when present, else exponential backoff.
            wait = float(resp.headers.get("retry-after") or 0) or (2 ** attempt + 1.0)
            time.sleep(min(wait, 30))
            continue
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        return [normalize(e, sub) for e in root.findall("a:entry", ATOM)]
    return []


def scrape(
    subreddits: list[str] | None = None,
    listing: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Pull recent posts across the watchlist. One bad subreddit never fails the run."""
    subs = subreddits or settings.subreddit_list
    listing = listing or settings.reddit_listing
    limit = limit or settings.reddit_limit

    posts: list[dict] = []
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        for i, sub in enumerate(subs):
            try:
                posts.extend(_fetch(sub, listing, limit, client))
            except Exception as exc:  # noqa: BLE001 — resilience over strictness here
                print(f"[scrape] r/{sub} failed: {exc}")
            if i < len(subs) - 1:
                time.sleep(settings.reddit_request_delay)
    return posts
