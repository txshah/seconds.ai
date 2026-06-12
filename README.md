# seconds.ai — Consumer Lawsuit Intelligence Agent

> Built at the **Harness Engineering Hack** · June 12, 2026 · AWS Builder Loft, SF

Consumers get scammed every day and never know they have legal recourse. Lawyers who specialize in consumer protection struggle to find clients at the exact moment they're venting about being wronged. **seconds.ai** closes that gap: a fully autonomous agent pipeline that monitors public discourse, detects potential class-action and individual consumer lawsuits in real time, and connects affected consumers with relevant attorneys via email — all cited, traceable, and live on the web.

---

## ✅ Built & live — start here (for the team)

The **ingestion + data layer** is built and running on ClickHouse **Cloud**. If
you're picking this up, the one doc to read is **[HANDOFF.md](HANDOFF.md)** — it is
the contract (DB creds, the `posts` view to read, the `rankings` table to write,
and the `/leads` + `/cases` API).

| Layer | Where | Status |
|---|---|---|
| Scraping | Guild.ai + Firecrawl (`guild/`); Reddit RSS backup (`app/scraper/`) | ✅ working |
| Storage | ClickHouse **Cloud** (`app/schema.sql`) — 75+ real leads | ✅ live |
| Handoff API | FastAPI (`app/api.py`) — `/leads`, `/cases`, `/leads/export.jsonl` | ✅ working |
| Ranking (Pioneer) | reads `posts` view → writes `rankings` table | 🟡 handoff ready |
| Cite / Email / Deploy | Senso / Composio / Render | ⬜ downstream |

**Run locally** (no accounts needed for the RSS path):
```bash
make install     # deps into .venv
make db          # local ClickHouse (separate terminal) — or set CLICKHOUSE_* for Cloud
make bootstrap   # create schema
make ingest      # one scrape -> store cycle (Reddit RSS)
make serve       # API on :8000  (docs at /docs)
```
Cloud / Firecrawl / Guild / Render setup → **[DEPLOY.md](DEPLOY.md)**.

---

## Problem Statement

Millions of consumer complaints surface on Reddit, forums, and social media every day — data breaches, subscription traps, defective products, deceptive advertising. Most consumers don't know they have legal standing. Most plaintiff attorneys don't see these signals until months later, when a class is already certified and the moment has passed.

**seconds.ai** turns that lag into seconds.

---

## What It Does

1. **Ingest** — A scheduled Guild.ai agent polls Reddit continuously, pulling posts from communities like r/legaladvice, r/mildlyinfuriating, r/personalfinance, and company-specific subreddits. Every complaint is a potential lead.

2. **Store & Trace** — Raw posts and extracted entities land in ClickHouse. Every row carries a full provenance chain: source URL, subreddit, timestamp, ingestion run ID. Judges and end-users can see exactly how each finding was surfaced.

3. **Rank** — A Pioneer-fine-tuned model scores each complaint for legal relevance: Is this a pattern? Is there an existing class action? What federal or state statute might apply? The model outputs a ranked list with confidence scores.

4. **Cite** — Senso.ai publishes the agent's reasoning and sources as a live, citable web artifact. Every conclusion the agent reaches links back to the evidence. `cited.md` (auto-generated per run) provides a markdown-formatted citation trail for transparency.

5. **Act** — Composio connects to Gmail and sends two targeted emails per qualifying lead:
   - A plain-language summary to the **consumer** explaining their potential recourse.
   - A structured lead alert to **subscribed attorneys** filtered by practice area and jurisdiction.

6. **Deploy & Monitor** — The full pipeline runs on Render. Guild.ai orchestrates the schedule, Langfuse traces every LLM call, and ClickHouse dashboards show real-time pipeline health.

---

## Architecture

```
Guild.ai agent (scheduled)  ──Firecrawl search: Reddit + open web──┐
   (app/scraper RSS = local/backup source)                        │
                                                                   ▼
                                  POST /leads/ingest  →  enrich · dedup · store
                                                                   │
                                                                   ▼
        ClickHouse Cloud   tables: raw_posts · leads · rankings · ingest_runs
                           views:  posts (Pioneer read) · case_signals (class-action rollup)
                                                                   │
                                                                   ▼
        Pioneer  — reads `posts`, ranks, writes `rankings` → rolls up into case_signals
                                                                   │
                                                                   ▼
        Senso.ai (cite)   ·   Composio → Gmail (alert consumer + law firm)   ·   Render (host)
```

---

## Sponsor Tool Usage

| Tool | Role in Pipeline |
|---|---|
| **Guild.ai** | Orchestrates the ingestion agent on a recurring timer; manages agent lifecycle and retries |
| **ClickHouse** | Stores all raw and processed records with full analytic capability; powers the provenance/trace dashboard |
| **Pioneer** | Fine-tuned model that ranks complaints by legal relevance, classifies statute type, and filters noise |
| **Composio** | Gmail integration that sends personalized consumer alerts and attorney lead emails without manual intervention |
| **Render** | Hosts the API layer, agent runner, and dashboard; zero-downtime deploys |
| **Senso.ai** | Publishes agent outputs as citable, web-accessible artifacts — the agent's reasoning is transparent and linkable |

---

## cited.md

Every pipeline run generates a `cited.md` file that documents:

- The source posts used (Reddit URL, timestamp, subreddit)
- The ClickHouse query that retrieved them
- The Pioneer model version and score thresholds applied
- The final consumer/attorney outputs with reasoning

This file is auto-committed to the repo and published via Senso.ai so any output the agent produces is fully auditable.

---

## Autonomy

The agent requires **zero manual intervention** after deploy:

- Guild.ai fires the ingestion job on schedule
- ClickHouse deduplicates re-ingested posts automatically
- Pioneer scores and filters without human review
- Composio sends emails only when confidence exceeds threshold
- Senso.ai publishes the updated citation page after each run

A human only steps in to tune the Pioneer ranking threshold or add new subreddits to the watch list.

---

## Setup

```bash
git clone https://github.com/txshah/seconds.ai
cd seconds.ai
cp .env.example .env        # fill in CLICKHOUSE_* (Cloud) and FIRECRAWL_API_KEY
make install                # deps into .venv

make bootstrap              # create the ClickHouse schema
make ingest                 # Reddit RSS         -> ClickHouse
make firecrawl              # Firecrawl (web+reddit) -> ClickHouse  (needs FIRECRAWL_API_KEY)
make serve                  # handoff API on :8000  (docs at /docs)
```

### Environment Variables (this layer)

Full list with comments in [.env.example](.env.example). The ingestion + DB layer needs:

```
CLICKHOUSE_HOST=        # <service>.<region>.clickhouse.cloud   (PORT=8443, SECURE=true)
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=
FIRECRAWL_API_KEY=      # for the Guild.ai / Firecrawl acquisition path
```

Downstream teammates add their own keys (Pioneer / Composio / Senso / Guild / Render).

---

## Judging Alignment

| Criterion (20% each) | How seconds.ai addresses it |
|---|---|
| **Idea** | Consumer protection is a proven pain point; the attorney lead-gen creates a sustainable revenue model alongside the consumer-facing utility |
| **Technical Implementation** | End-to-end pipeline from ingestion → ranking → email → web publish, all wired together with real APIs |
| **Tool Use** | Six sponsor tools each handle the job they're best at; no gratuitous use |
| **Presentation** | 3-minute demo walks through a live Reddit post being ingested, scored, emailed, and cited in real time |
| **Autonomy** | Once deployed, the agent monitors, decides, and acts without any human in the loop |

---

## Prize Targets

- Most Innovative Use of Agents — **Guild.ai** ($2,800)
- Best Use of **ClickHouse** ($1,600)
- Best Use of **Pioneer** ($500)
- Best Agent Execution — **Composio** ($200)
- Best Use of **Render** ($1,000 credits)
- Best Use of **Senso.ai** ($2,000 credits)

---

## Team

Built in one day at the Harness Engineering Hack, June 12, 2026.

---

## License

MIT
