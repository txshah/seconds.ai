"""seconds.ai ingestion + handoff API.

This is the interface the rest of the team builds on:

  POST /ingest/run          run one scrape->store cycle   (Guild.ai triggers this)
  GET  /leads               ranking-ready leads for Pioneer (filter + sort)
  GET  /leads/{id}          one lead with full provenance
  GET  /leads/export.jsonl  streamed JSONL dataset for fine-tuning / batch scoring
  POST /leads/{id}/rank     Pioneer writes a score back onto a lead
  GET  /runs                recent ingest-run provenance
  GET  /stats               pipeline health / dashboard numbers
  GET  /health              liveness + ClickHouse reachability

Interactive docs at /docs.
"""

from __future__ import annotations

import json
from typing import Iterator

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import StreamingResponse

from . import store
from .config import settings
from .db import bootstrap, get_client
from .models import (
    CaseSignal, IngestItemsRequest, IngestRequest, IngestResult, LeadOut,
    RankRequest, RunOut, Stats,
)
from .pipeline import ingest_items, run_ingest

app = FastAPI(
    title="seconds.ai — ingestion & handoff API",
    version="0.1.0",
    description="Reddit -> ClickHouse lead pipeline. Ranking-ready handoff for Pioneer.",
)

_ORDER_COLUMNS = {"signal_score", "created_utc", "pioneer_score", "ingested_at"}


@app.on_event("startup")
def _startup() -> None:
    # Make sure tables exist so the API is usable on a fresh database.
    try:
        bootstrap()
    except Exception as exc:  # noqa: BLE001
        print(f"[startup] bootstrap skipped: {exc}")


# --------------------------------------------------------------------------- #
# health / stats
# --------------------------------------------------------------------------- #
@app.get("/health")
def health() -> dict:
    try:
        get_client().command("SELECT 1")
        return {"status": "ok", "clickhouse": "up", "database": settings.clickhouse_database}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(503, f"clickhouse unreachable: {exc}")


@app.get("/stats", response_model=Stats)
def stats() -> Stats:
    c = get_client()
    total = c.command("SELECT count() FROM leads FINAL")
    unranked = c.command("SELECT count() FROM leads FINAL WHERE status = 'unranked'")
    ranked = c.command("SELECT count() FROM leads FINAL WHERE status = 'ranked'")
    by_type = {
        row[0]: row[1]
        for row in c.query(
            "SELECT complaint_type, count() FROM leads FINAL GROUP BY complaint_type ORDER BY 2 DESC"
        ).result_rows
    }
    top_companies = {
        row[0]: row[1]
        for row in c.query(
            "SELECT arrayJoin(companies) AS co, count() AS n FROM leads FINAL "
            "GROUP BY co ORDER BY n DESC LIMIT 10"
        ).result_rows
    }
    last_run = c.command("SELECT max(finished_at) FROM ingest_runs")
    total_runs = c.command("SELECT count() FROM ingest_runs")
    return Stats(
        total_leads=int(total),
        unranked=int(unranked),
        ranked=int(ranked),
        by_complaint_type=by_type,
        top_companies=top_companies,
        last_run_at=last_run if str(last_run) != "1970-01-01 00:00:00" else None,
        total_runs=int(total_runs),
    )


# --------------------------------------------------------------------------- #
# ingestion (Guild.ai trigger target)
# --------------------------------------------------------------------------- #
@app.post("/ingest/run", response_model=IngestResult)
def ingest(req: IngestRequest | None = None,
           x_ingest_token: str | None = Header(default=None)) -> IngestResult:
    if settings.api_ingest_token and x_ingest_token != settings.api_ingest_token:
        raise HTTPException(401, "invalid or missing X-Ingest-Token")
    req = req or IngestRequest()
    result = run_ingest(subreddits=req.subreddits, listing=req.listing, limit=req.limit)
    return IngestResult(**result)


# --------------------------------------------------------------------------- #
# leads handoff
# --------------------------------------------------------------------------- #
def _query_leads(filters: dict, limit: int, offset: int,
                 order_by: str, order: str) -> list[dict]:
    conditions, params = [], {}
    if filters.get("status"):
        conditions.append("status = %(status)s")
        params["status"] = filters["status"]
    if filters.get("complaint_type"):
        conditions.append("complaint_type = %(complaint_type)s")
        params["complaint_type"] = filters["complaint_type"]
    if filters.get("subreddit"):
        conditions.append("lower(subreddit) = lower(%(subreddit)s)")
        params["subreddit"] = filters["subreddit"]
    if filters.get("min_score") is not None:
        conditions.append("signal_score >= %(min_score)s")
        params["min_score"] = filters["min_score"]
    if filters.get("company"):
        conditions.append(
            "arrayExists(x -> position(lower(x), lower(%(company)s)) > 0, companies)"
        )
        params["company"] = filters["company"]
    if filters.get("q"):
        conditions.append("position(lower(text), lower(%(q)s)) > 0")
        params["q"] = filters["q"]

    where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
    col = order_by if order_by in _ORDER_COLUMNS else "signal_score"
    direction = "ASC" if order.lower() == "asc" else "DESC"
    params["limit"] = limit
    params["offset"] = offset

    sql = (
        f"SELECT * FROM leads FINAL{where} "
        f"ORDER BY {col} {direction}, created_utc DESC "
        f"LIMIT %(limit)s OFFSET %(offset)s"
    )
    res = get_client().query(sql, parameters=params)
    return [dict(zip(res.column_names, row)) for row in res.result_rows]


@app.get("/leads", response_model=list[LeadOut])
def list_leads(
    status: str | None = Query(None, description="unranked | ranked | sent"),
    complaint_type: str | None = None,
    subreddit: str | None = None,
    company: str | None = None,
    q: str | None = Query(None, description="substring search over text"),
    min_score: float | None = Query(None, ge=0, le=1),
    order_by: str = Query("signal_score", description="signal_score|created_utc|pioneer_score|ingested_at"),
    order: str = Query("desc"),
    limit: int = Query(50, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> list[LeadOut]:
    rows = _query_leads(
        {"status": status, "complaint_type": complaint_type, "subreddit": subreddit,
         "company": company, "q": q, "min_score": min_score},
        limit, offset, order_by, order,
    )
    return [LeadOut(**r) for r in rows]


@app.get("/leads/export.jsonl")
def export_jsonl(
    status: str | None = None,
    min_score: float = Query(0.0, ge=0, le=1),
    limit: int = Query(5000, ge=1, le=100000),
) -> StreamingResponse:
    """Streamed JSONL — the dataset format Pioneer fine-tuning / batch scoring expects.

    One line per lead: {lead_id, text, label, metadata{...}}.
    """
    rows = _query_leads(
        {"status": status, "min_score": min_score},
        limit=limit, offset=0, order_by="signal_score", order="desc",
    )

    def gen() -> Iterator[str]:
        for r in rows:
            yield json.dumps({
                "lead_id": r["lead_id"],
                "text": r["text"],
                "label": r["pioneer_label"],
                "metadata": {
                    "source": r["source"],
                    "source_url": r["source_url"],
                    "subreddit": r["subreddit"],
                    "companies": r["companies"],
                    "complaint_type": r["complaint_type"],
                    "money_mentioned": r["money_mentioned"],
                    "signal_score": r["signal_score"],
                    "created_utc": r["created_utc"].isoformat(),
                },
            }, ensure_ascii=False) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.post("/leads/ingest", response_model=IngestResult)
def ingest_scraped(req: IngestItemsRequest,
                   x_ingest_token: str | None = Header(default=None)) -> IngestResult:
    """Accept already-scraped items (Guild.ai + Firecrawl) and store them through
    the same enrich/dedup path as our own scraper. This is the seam that lets the
    scraping live in Guild while storage + the Pioneer contract stay here."""
    if settings.api_ingest_token and x_ingest_token != settings.api_ingest_token:
        raise HTTPException(401, "invalid or missing X-Ingest-Token")
    result = ingest_items([i.model_dump() for i in req.items], source_label=req.source_label)
    return IngestResult(**result)


@app.get("/leads/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: str) -> LeadOut:
    row = store.get_lead(get_client(), lead_id)
    if row is None:
        raise HTTPException(404, f"lead {lead_id} not found")
    return LeadOut(**row)


@app.post("/leads/{lead_id}/rank", response_model=LeadOut)
def rank_lead(lead_id: str, req: RankRequest) -> LeadOut:
    updated = store.set_ranking(
        get_client(), lead_id, req.score, req.label, req.model, req.status
    )
    if updated is None:
        raise HTTPException(404, f"lead {lead_id} not found")
    return LeadOut(**updated)


# --------------------------------------------------------------------------- #
# cases — the class-action product surface (leads rolled up by company x issue)
# --------------------------------------------------------------------------- #
_CASE_ORDER = {"case_score", "complainants", "complainants_7d",
               "complainants_30d", "mentions", "last_seen"}


@app.get("/cases", response_model=list[CaseSignal])
def list_cases(
    complaint_type: str | None = None,
    company: str | None = Query(None, description="substring match on company"),
    min_complainants: int = Query(1, ge=1, description="numerosity floor"),
    min_score: float = Query(0.0, ge=0, le=1),
    order_by: str = Query("case_score", description="case_score|complainants|complainants_7d|mentions|last_seen"),
    limit: int = Query(50, ge=1, le=500),
) -> list[CaseSignal]:
    conditions = ["complainants >= %(minc)s", "case_score >= %(mins)s"]
    params: dict = {"minc": min_complainants, "mins": min_score}
    if complaint_type:
        conditions.append("complaint_type = %(ct)s")
        params["ct"] = complaint_type
    if company:
        conditions.append("position(lower(company), lower(%(co)s)) > 0")
        params["co"] = company
    col = order_by if order_by in _CASE_ORDER else "case_score"
    params["limit"] = limit
    sql = (
        f"SELECT * FROM case_signals WHERE {' AND '.join(conditions)} "
        f"ORDER BY {col} DESC, complainants DESC LIMIT %(limit)s"
    )
    res = get_client().query(sql, parameters=params)
    return [CaseSignal(**dict(zip(res.column_names, row))) for row in res.result_rows]


# --------------------------------------------------------------------------- #
# provenance
# --------------------------------------------------------------------------- #
@app.get("/runs", response_model=list[RunOut])
def list_runs(limit: int = Query(20, ge=1, le=200)) -> list[RunOut]:
    res = get_client().query(
        "SELECT run_id, source, query, started_at, finished_at, posts_fetched, "
        "posts_new, leads_created, status, error FROM ingest_runs "
        "ORDER BY started_at DESC LIMIT %(limit)s",
        parameters={"limit": limit},
    )
    return [RunOut(**dict(zip(res.column_names, row))) for row in res.result_rows]
