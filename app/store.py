"""Storage layer: dedup, batch inserts, run logging, and score write-back.

All writes go through clickhouse-connect. raw_posts and leads are
ReplacingMergeTree tables keyed on the natural id, so re-ingesting a post is
safe — the newest version (by ingested_at / updated_at) wins after merge, and
reads use FINAL to collapse duplicates deterministically.
"""

from __future__ import annotations

from datetime import datetime, timezone

from clickhouse_connect.driver import Client

RAW_COLUMNS = [
    "post_id", "source", "subreddit", "author", "title", "body", "permalink",
    "external_url", "created_utc", "score", "num_comments", "run_id",
    "ingested_at", "raw_json",
]

LEAD_COLUMNS = [
    "lead_id", "source", "source_url", "subreddit", "created_utc", "author",
    "title", "body", "text", "companies", "complaint_type", "keywords",
    "money_mentioned", "signal_score", "status", "pioneer_score", "pioneer_label",
    "pioneer_model", "ranked_at", "run_id", "ingested_at", "updated_at",
]

RUN_COLUMNS = [
    "run_id", "source", "query", "started_at", "finished_at",
    "posts_fetched", "posts_new", "leads_created", "status", "error",
]


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def existing_post_ids(client: Client, post_ids: list[str]) -> set[str]:
    """Which of these post_ids are already stored (for accurate new-post counts)."""
    if not post_ids:
        return set()
    rows = client.query(
        "SELECT DISTINCT post_id FROM raw_posts WHERE post_id IN %(ids)s",
        parameters={"ids": post_ids},
    ).result_rows
    return {r[0] for r in rows}


def insert_raw_posts(client: Client, posts: list[dict], run_id: str) -> None:
    if not posts:
        return
    now = _now()
    data = [
        [
            p["post_id"], p["source"], p["subreddit"], p["author"], p["title"],
            p["body"], p["permalink"], p["external_url"], p["created_utc"],
            p["score"], p["num_comments"], run_id, now, p["raw_json"],
        ]
        for p in posts
    ]
    client.insert("raw_posts", data, column_names=RAW_COLUMNS)


def insert_leads(client: Client, leads: list[dict], run_id: str) -> None:
    if not leads:
        return
    now = _now()
    data = [
        [
            lead["lead_id"], lead["source"], lead["source_url"], lead["subreddit"],
            lead["created_utc"], lead["author"], lead["title"], lead["body"],
            lead["text"], lead["companies"], lead["complaint_type"], lead["keywords"],
            lead["money_mentioned"], lead["signal_score"], "unranked",
            None, None, None, None, run_id, now, now,
        ]
        for lead in leads
    ]
    client.insert("leads", data, column_names=LEAD_COLUMNS)


def log_run(client: Client, run: dict) -> None:
    data = [[
        run["run_id"], run["source"], run["query"], run["started_at"],
        run["finished_at"], run["posts_fetched"], run["posts_new"],
        run["leads_created"], run["status"], run.get("error", ""),
    ]]
    client.insert("ingest_runs", data, column_names=RUN_COLUMNS)


def get_lead(client: Client, lead_id: str) -> dict | None:
    res = client.query(
        "SELECT * FROM leads FINAL WHERE lead_id = %(id)s LIMIT 1",
        parameters={"id": lead_id},
    )
    if not res.result_rows:
        return None
    return dict(zip(res.column_names, res.result_rows[0]))


def set_ranking(
    client: Client,
    lead_id: str,
    score: float,
    label: str | None,
    model: str | None,
    status: str = "ranked",
) -> dict | None:
    """Pioneer write-back: re-insert the lead row with ranking fields set.

    ReplacingMergeTree keeps the row with the newest updated_at, so this is an
    idempotent upsert — Pioneer can re-rank a lead any number of times.
    """
    current = get_lead(client, lead_id)
    if current is None:
        return None
    now = _now()
    current.update(
        pioneer_score=float(score),
        pioneer_label=label,
        pioneer_model=model,
        ranked_at=now,
        status=status,
        updated_at=now,
    )
    row = [current[c] for c in LEAD_COLUMNS]
    client.insert("leads", [row], column_names=LEAD_COLUMNS)
    return current
