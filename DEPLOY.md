# Deploy

## Render (public API for Guild to call)
The repo has `render.yaml`, `Procfile`, and `runtime.txt`. On Render → New →
Blueprint → connect this repo. It builds `pip install -r requirements.txt` and
starts `uvicorn app.api:app --host 0.0.0.0 --port $PORT`, health-checking `/health`.

Set these secrets in the Render dashboard (marked `sync: false` in `render.yaml`):
`CLICKHOUSE_HOST`, `CLICKHOUSE_PASSWORD`, `API_INGEST_TOKEN` (optional), `FIRECRAWL_API_KEY`.

> Render can't reach a local ClickHouse — use **ClickHouse Cloud** (port 8443,
> `CLICKHOUSE_SECURE=true`). Same code, env-only change.

## ClickHouse Cloud (shared DB for the team)
1. Create a free service at clickhouse.cloud.
2. Put host + password in `.env` (or Render): `CLICKHOUSE_PORT=8443`,
   `CLICKHOUSE_SECURE=true`.
3. `make bootstrap` once to create the schema. Done — teammates and Pioneer all
   query the same `seconds.leads`.

## Guild.ai agent (autonomous scraping)
See `guild/README.md`. Connect the Firecrawl credential, `guild deploy
ingest-agent.ts`, then create an hourly time trigger pointed at
`https://<service>.onrender.com` with your `firecrawlKey`.

## Local (no accounts needed)
```bash
make db          # ClickHouse (separate terminal)
make bootstrap
make ingest      # Reddit RSS source
make firecrawl   # Firecrawl source (needs FIRECRAWL_API_KEY)
make serve       # API on :8000  (/docs)
```
