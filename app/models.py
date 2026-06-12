"""Pydantic models — these ARE the handoff contract for Pioneer & the team.

A `LeadOut` is one ranking candidate: clean text + extracted entities + a cheap
pre-score + full provenance + a write-back slot for Pioneer's score.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class LeadOut(BaseModel):
    lead_id: str
    source: str
    source_url: str                      # provenance: click through to the original post
    subreddit: str
    created_utc: datetime
    author: str
    title: str
    body: str
    text: str                            # title + body — the model input
    companies: list[str]
    complaint_type: str
    keywords: list[str]
    money_mentioned: int
    signal_score: float                  # cheap 0..1 heuristic pre-score
    status: str                          # unranked | ranked | sent
    pioneer_score: float | None = None   # written back by Pioneer
    pioneer_label: str | None = None
    pioneer_model: str | None = None
    ranked_at: datetime | None = None
    run_id: str
    ingested_at: datetime
    updated_at: datetime


class IngestResult(BaseModel):
    run_id: str
    status: str
    error: str | None = None
    posts_fetched: int
    posts_new: int
    leads_created: int
    duration_s: float


class IngestRequest(BaseModel):
    subreddits: list[str] | None = None
    listing: str | None = None
    limit: int | None = None


class ScrapedItem(BaseModel):
    """One item handed in by an external scraper (Guild.ai + Firecrawl)."""
    source: str = Field("web", description="reddit | web | news | ...")
    source_url: str = Field(..., description="canonical URL — provenance + dedup key")
    title: str = ""
    body: str = ""
    subreddit: str = ""
    author: str = ""
    created_utc: str | None = Field(None, description="ISO-8601; defaults to now")
    external_url: str = ""
    native_id: str | None = Field(None, description="source-native id, if known")


class IngestItemsRequest(BaseModel):
    items: list[ScrapedItem]
    source_label: str = Field("firecrawl", description="recorded on the ingest run")


class RankRequest(BaseModel):
    score: float = Field(..., ge=0.0, le=1.0, description="Pioneer relevance score 0..1")
    label: str | None = Field(None, description="Statute / class-action tag")
    model: str | None = Field(None, description="Pioneer model id/version")
    status: str = Field("ranked", description="ranked | sent")


class RunOut(BaseModel):
    run_id: str
    source: str
    query: str
    started_at: datetime
    finished_at: datetime
    posts_fetched: int
    posts_new: int
    leads_created: int
    status: str
    error: str


class CaseSignal(BaseModel):
    """A class-action candidate: leads rolled up by (company x complaint_type).
    This is the law-firm-facing product surface."""
    company: str
    complaint_type: str
    complainants: int                    # DISTINCT people — the numerosity signal
    mentions: int                        # total posts (>= complainants)
    complainants_7d: int
    complainants_30d: int
    avg_signal: float
    avg_pioneer_score: float | None = None
    money_share: float                   # fraction of members mentioning $ harm
    first_seen: datetime
    last_seen: datetime
    sources: list[str]
    statutes: list[str]                  # Pioneer-assigned statute tags (once ranked)
    evidence: list[str]                  # sample member source_urls (provenance)
    case_score: float                    # heuristic class-action viability 0..1


class Stats(BaseModel):
    total_leads: int
    unranked: int
    ranked: int
    by_complaint_type: dict[str, int]
    top_companies: dict[str, int]
    last_run_at: datetime | None = None
    total_runs: int
