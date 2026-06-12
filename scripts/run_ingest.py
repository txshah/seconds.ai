#!/usr/bin/env python
"""CLI wrapper around one ingestion cycle.

    python scripts/run_ingest.py                     # use the configured watchlist
    python scripts/run_ingest.py --subs scams,consumer --limit 25
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.pipeline import run_ingest  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one seconds.ai ingestion cycle")
    ap.add_argument("--subs", help="comma-separated subreddits (overrides default watchlist)")
    ap.add_argument("--listing", default=None, help="new | hot | rising | top")
    ap.add_argument("--limit", type=int, default=None, help="posts per subreddit")
    args = ap.parse_args()

    subs = [s.strip() for s in args.subs.split(",")] if args.subs else None
    result = run_ingest(subreddits=subs, listing=args.listing, limit=args.limit)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
