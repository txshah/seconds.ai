# seconds.ai — Data & API Handoff

**Owner of this layer:** ingestion (Guild.ai) + storage (ClickHouse) + handoff API.
**Consumers:** Pioneer (ranking), Senso.ai (citations), Composio (email).

This document is the contract. The scraping/DB internals can change freely as
long as the **`leads` shape** and the **endpoints** below stay stable.

---

## TL;DR for Pioneer

You have three equivalent ways to get ranking-ready leads. Pick whichever fits.

1. **Pull the queue (REST)** — newest, highest-signal first:
   ```bash
   curl "$API/leads?status=unranked&order_by=signal_score&limit=100"
   ```
2. **Stream a dataset (JSONL)** — for fine-tuning or batch scoring:
   ```bash
   curl "$API/leads/export.jsonl?min_score=0.3" -o leads.jsonl
   ```
3. **Query ClickHouse directly** — full analytical access:
   ```sql
   SELECT lead_id, text, companies, complaint_type, signal_score, source_url
   FROM seconds.leads FINAL
   WHERE status = 'unranked'
   ORDER BY signal_score DESC
   LIMIT 100;
   ```

Then **write your score back** so the rest of the pipeline (email/citations) can act:
```bash
curl -X POST "$API/leads/$LEAD_ID/rank" -H 'Content-Type: application/json' \
  -d '{"score":0.92,"label":"TCPA-robocall","model":"pioneer-rank-v1"}'
```

`text` is your model input. `signal_score` is a cheap recall-oriented pre-score
(intentionally loose) — your `pioneer_score` is the real ranking.

---

## For Pioneer — direct ClickHouse (the DB handoff)

Work straight against ClickHouse with the shared creds — no app code required.

**Connect**
```
host:     tqof521f2x.us-west-2.aws.clickhouse.cloud
port:     8443   (HTTPS / secure)
user:     default
password: (shared privately — not in this repo)
database: seconds
```
```python
import clickhouse_connect
c = clickhouse_connect.get_client(host="…clickhouse.cloud", port=8443,
        username="default", password="…", secure=True, database="seconds")
```

**Read — the `posts` view** (shaped to your requested spec, always fresh):

| column | |
|---|---|
| `post_id` | stable id (Reddit id, no `t3_`) — your join key |
| `reddit_fullname` | `t3_<id>` form if you want it |
| `platform` | `reddit` / `web` / … |
| `taxonomy` | subreddit (or source tag) |
| `user_id` | author |
| `post` | title + body — the model input |
| `title`, `source_url`, `created_utc` | provenance + content |
| `metrics_json` | `{"score":N,"num_comments":M}` |
| `raw_json` | full original object |
| `companies`,`complaint_type`,`keywords`,`money_mentioned`,`signal_score` | our enrichment (use or ignore) |
| `pioneer_score`,`pioneer_label` | your live ranking, joined back in |

```sql
SELECT post_id, post, companies, complaint_type, signal_score, source_url
FROM seconds.posts
WHERE pioneer_score IS NULL            -- not yet ranked
ORDER BY signal_score DESC
LIMIT 100;
```

**Write — the `rankings` table** (one INSERT per scored post; newest wins):
```sql
INSERT INTO seconds.rankings (post_id, pioneer_score, pioneer_label, pioneer_model)
VALUES ('abc123', 0.92, 'TCPA-47USC227', 'pioneer-rank-v1');
```
The score immediately appears in `posts.pioneer_score` **and** rolls up into the
`case_signals` product view (`avg_pioneer_score`, `case_score`). Re-ranking is
just another INSERT. (Prefer HTTP? Same effect via `POST /leads/{post_id}/rank`.)

---

## The `lead` record (handoff schema)

Every lead is one ranking candidate. Same fields in the API JSON and the
ClickHouse `leads` table.

| field             | type            | meaning |
|-------------------|-----------------|---------|
| `lead_id`         | string          | stable id (= Reddit post id). Idempotent key for write-back. |
| `source`          | string          | `reddit` (more sources later) |
| `source_url`      | string          | **provenance** — click through to the original post |
| `subreddit`       | string          | origin community |
| `created_utc`     | datetime (UTC)  | when the post was made |
| `author`          | string          | poster handle |
| `title`           | string          | post title |
| `body`            | string          | post body (HTML-stripped) |
| `text`            | string          | `title + "\n\n" + body` — **the model input** |
| `companies`       | string[]        | detected brand/company mentions |
| `complaint_type`  | string          | `data_breach \| subscription_trap \| defective_product \| deceptive_advertising \| billing_dispute \| debt_collection \| privacy_spam \| other` |
| `keywords`        | string[]        | matched signal terms (explainability) |
| `money_mentioned` | 0/1             | a dollar amount appears in the text |
| `signal_score`    | float 0..1      | cheap heuristic pre-score (recall-oriented) |
| `status`          | string          | `unranked \| ranked \| sent` |
| `pioneer_score`   | float 0..1 \| null | **you write this** |
| `pioneer_label`   | string \| null  | **you write this** — statute / class-action tag |
| `pioneer_model`   | string \| null  | **you write this** — model id/version |
| `ranked_at`       | datetime \| null | set automatically on write-back |
| `run_id`          | string          | which ingest run produced it → `ingest_runs` |
| `ingested_at`     | datetime (UTC)  | when we stored it |
| `updated_at`      | datetime (UTC)  | version column (latest wins) |

### Example (`GET /leads/{id}`)
```json
{
  "lead_id": "1u3z8bc",
  "source": "reddit",
  "source_url": "https://www.reddit.com/r/legaladvice/comments/1u3z8bc/federal_employment_lawsuit_advice/",
  "subreddit": "legaladvice",
  "created_utc": "2026-06-12T22:54:43",
  "title": "Federal employment lawsuit advice",
  "text": "Federal employment lawsuit advice\n\nMy location: NJ ...",
  "companies": [],
  "complaint_type": "other",
  "keywords": ["attorney", "lawsuit", "retaliation", "settlement"],
  "money_mentioned": 1,
  "signal_score": 0.65,
  "status": "unranked",
  "pioneer_score": null,
  "run_id": "fd372e04...",
  "ingested_at": "2026-06-13T02:08:02"
}
```

---

## Two layers: leads vs cases

A single complaint isn't a class action — a **cluster** is. So there are two
handoff surfaces, and Pioneer only has to deal with the first:

```
/leads   individual complaints   = the class members / evidence   ← Pioneer ranks these
/cases   company × issue rollup  = the PRODUCT a law firm buys     ← composed from leads
```

- **Pioneer ranks leads** (unchanged): pull `/leads?status=unranked`, score, `POST /rank`.
- **`/cases` composes those scores** — `case_score` is built from *distinct
  complainants + 7d velocity + avg signal/pioneer_score*. No extra work for Pioneer.
- **Downstream consumes the right level:** Composio emails firms the top **cases**
  (with the evidence trail); Senso cites the **case**; consumer alerts come from the **lead**.

### `GET /cases` — class-action candidate

Filters: `complaint_type`, `company` (substring), `min_complainants` (numerosity
floor), `min_score`, `order_by` (`case_score|complainants|complainants_7d|mentions|last_seen`), `limit`.

| field | meaning |
|---|---|
| `company`, `complaint_type` | the (defendant × harm) the cluster is about |
| `complainants` | **DISTINCT** people complaining — the numerosity signal |
| `mentions` | total posts (≥ complainants) |
| `complainants_7d` / `_30d` | distinct complainants in the trailing window (velocity) |
| `avg_signal` | mean heuristic pre-score of members |
| `avg_pioneer_score` | mean Pioneer score once members are ranked (null until then) |
| `money_share` | fraction of members citing a dollar harm |
| `first_seen` / `last_seen` | cluster age + freshness |
| `sources` | reddit / web / ... |
| `statutes` | Pioneer statute tags aggregated up from members |
| `evidence` | sample member `source_url`s — the provenance trail for the firm |
| `case_score` | heuristic class-action viability 0..1 (Pioneer scores refine it) |

> Built as a ClickHouse view over `leads FINAL` (`uniqExact(author)` for
> numerosity, `arrayJoin(companies)` to attribute, windowed `uniqExactIf` for
> velocity). Always fresh; scales as a real-time aggregation.

---

## API reference

Base URL: local `http://127.0.0.1:8000`, prod `https://<app>.onrender.com`.
Interactive docs + schemas at **`/docs`**.

| method | path | purpose |
|--------|------|---------|
| `POST` | `/leads/ingest` | accept already-scraped items (Guild.ai + Firecrawl) → enrich/dedup/store |
| `POST` | `/ingest/run` | we pull Reddit RSS ourselves (local-dev / backup source) |
| `GET`  | `/leads` | list leads — filters below |
| `GET`  | `/leads/{id}` | one lead with full provenance |
| `GET`  | `/leads/export.jsonl` | streamed JSONL dataset |
| `POST` | `/leads/{id}/rank` | Pioneer score write-back |
| `GET`  | `/cases` | class-action candidates (company × issue, ranked) |
| `GET`  | `/runs` | recent ingest-run provenance |
| `GET`  | `/stats` | pipeline health / dashboard numbers |
| `GET`  | `/health` | liveness + ClickHouse reachability |

**`GET /leads` query params:** `status`, `complaint_type`, `subreddit`,
`company` (substring), `q` (text search), `min_score`, `order_by`
(`signal_score|created_utc|pioneer_score|ingested_at`), `order` (`asc|desc`),
`limit` (≤1000), `offset`.

**`POST /leads/{id}/rank` body:**
```json
{ "score": 0.0-1.0, "label": "optional statute tag", "model": "optional id", "status": "ranked" }
```
Idempotent — re-ranking the same lead just overwrites (newest `updated_at` wins).

**`/leads/export.jsonl`** — one object per line:
```json
{"lead_id":"...","text":"...","label":null,"metadata":{"source_url":"...","subreddit":"...","companies":[...],"complaint_type":"...","money_mentioned":1,"signal_score":0.65,"created_utc":"..."}}
```

---

## Provenance (for Senso.ai / `cited.md` / judges)

Every finding traces back cleanly:

```
lead.source_url   → the exact public post
lead.run_id       → ingest_runs row (when, what was scanned, counts)
raw_posts FINAL   → the immutable original (incl. raw_json) keyed by post_id
```

```sql
-- full trail for one lead
SELECT l.lead_id, l.source_url, r.started_at, r.query, p.raw_json
FROM seconds.leads      AS l FINAL
JOIN seconds.ingest_runs AS r ON r.run_id = l.run_id
JOIN seconds.raw_posts   AS p FINAL ON p.post_id = l.lead_id
WHERE l.lead_id = '1u3z8bc';
```

---

## Running it

```bash
make install        # python deps into .venv
make db             # local ClickHouse (separate terminal; Docker-free)
make bootstrap      # create database + tables
make ingest         # one scrape→store cycle
make serve          # API on :8000  (docs at /docs)
make stats
```

### ClickHouse Cloud (shared, for the team)
Set these in `.env` and **nothing in the code changes** — same `clickhouse-connect`
path. This is how downstream teammates query the *same* data:
```
CLICKHOUSE_HOST=<service>.<region>.clickhouse.cloud
CLICKHOUSE_PORT=8443
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=<password>
CLICKHOUSE_SECURE=true
```

---

## Notes & upgrade paths
- **Ingestion (two paths, one writer):**
  - **Primary — Guild.ai + Firecrawl:** a scheduled Guild agent searches Reddit +
    the open web via Firecrawl and `POST`s items to `/leads/ingest`. Firecrawl's
    proxy layer avoids the datacenter-IP blocking that hits raw Reddit calls, and
    adds open-web sources. See `guild/`.
  - **Backup — our Reddit RSS scraper** (`/ingest/run`): the `/*.json` API is
    IP-blocked in 2026, so we read the public **RSS/Atom** feeds (no score/comment
    counts; ~25 items/sub/run). Useful for local dev without a Firecrawl key, or
    as a fallback. Reddit **OAuth** restores 100 req/min + full fields later.
  - Both paths funnel through the same enrich/dedup/store code, so leads are
    identical regardless of source. `lead_id` is the Reddit post id when we can
    parse it, else a stable hash of `source_url` (web results dedup cleanly too).
- **Enrichment** is deterministic heuristics today (`app/enrich.py`). Replacing it
  with an LLM extractor changes nothing here as long as the output keys hold.
- **Dedup:** `raw_posts`/`leads` are `ReplacingMergeTree` on the natural key; reads
  use `FINAL`. Re-ingesting a post is safe.
