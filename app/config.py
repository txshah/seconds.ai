"""Central configuration, loaded from environment / .env.

Defaults target a LOCAL clickhouse-server (the brew/binary install) so the
pipeline runs with zero setup. To point at ClickHouse Cloud, set:

    CLICKHOUSE_HOST=<xxx>.clickhouse.cloud
    CLICKHOUSE_PORT=8443
    CLICKHOUSE_USER=default
    CLICKHOUSE_PASSWORD=<password>
    CLICKHOUSE_SECURE=true

Nothing else changes — the same clickhouse-connect code path serves both.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="", extra="ignore")

    # --- ClickHouse ---
    clickhouse_host: str = "localhost"
    clickhouse_port: int = 8123          # 8123 local HTTP, 8443 Cloud HTTPS
    clickhouse_user: str = "default"
    clickhouse_password: str = ""
    clickhouse_secure: bool = False      # True for Cloud
    clickhouse_database: str = "seconds"

    # --- Reddit ingestion ---
    # Comma-separated subreddits to watch. Consumer-complaint rich communities.
    reddit_subreddits: str = (
        "legaladvice,consumer,personalfinance,mildlyinfuriating,"
        "scams,creditcards,smallbusiness,Banking"
    )
    reddit_listing: str = "new"          # new | hot | rising | top
    reddit_limit: int = 50               # posts per subreddit per run (max 100)
    reddit_request_delay: float = 2.5    # seconds between subreddit fetches (rate-limit safety)
    # Reddit's RSS feeds want a browser-like UA; non-browser UAs get 403.
    reddit_user_agent: str = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    )

    # --- Enrichment / ranking pre-filter ---
    # Leads below this heuristic score are still stored but flagged low-signal.
    signal_floor: float = 0.0            # store everything; Pioneer decides cutoff

    # --- Firecrawl (the Guild.ai acquisition path; mirrors guild/ingest-agent.ts) ---
    firecrawl_api_key: str = ""
    firecrawl_limit: int = 5             # results per query
    # False = search-only (fast, snippet body). True = also scrape each page for
    # full markdown (richer, but minutes not seconds). Snippets are enough signal.
    firecrawl_scrape: bool = False
    # ';'-separated because the queries themselves contain commas/spaces.
    firecrawl_queries: str = (
        "site:reddit.com overcharged hidden fee can't cancel refund denied;"
        "site:reddit.com data breach leaked my personal information lawsuit;"
        "site:reddit.com defective product injury recall class action;"
        "consumer class action complaint deceptive advertising 2026;"
        "subscription trap auto-renew kept charging debt collector harassment"
    )

    # --- API ---
    api_ingest_token: str = ""           # optional shared secret for POST /ingest/run

    @property
    def subreddit_list(self) -> list[str]:
        return [s.strip() for s in self.reddit_subreddits.split(",") if s.strip()]

    @property
    def firecrawl_query_list(self) -> list[str]:
        return [q.strip() for q in self.firecrawl_queries.split(";") if q.strip()]


settings = Settings()
