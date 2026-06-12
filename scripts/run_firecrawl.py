#!/usr/bin/env python
"""CLI: run one Firecrawl ingestion cycle (the Guild+Firecrawl path, locally).

    FIRECRAWL_API_KEY=fc-... python scripts/run_firecrawl.py
    FIRECRAWL_API_KEY=fc-... python scripts/run_firecrawl.py --limit 8 \
        --queries "site:reddit.com overcharged hidden fee;data breach lawsuit 2026"
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from app.pipeline import run_firecrawl  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description="Run one seconds.ai Firecrawl ingestion cycle")
    ap.add_argument("--queries", help="';'-separated search queries (overrides default)")
    ap.add_argument("--limit", type=int, default=None, help="results per query")
    args = ap.parse_args()

    queries = [q.strip() for q in args.queries.split(";") if q.strip()] if args.queries else None
    print(json.dumps(run_firecrawl(queries=queries, limit=args.limit), indent=2))


if __name__ == "__main__":
    main()
