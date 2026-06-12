# seconds.ai — Consumer Lawsuit Intelligence Agent

> Built at the **Harness Engineering Hack** · June 12, 2026 · AWS Builder Loft, SF

Consumers get scammed every day and never know they have legal recourse. Lawyers who specialize in consumer protection struggle to find clients at the exact moment they're venting about being wronged. **seconds.ai** closes that gap: a fully autonomous agent pipeline that monitors public discourse, detects potential class-action and individual consumer lawsuits in real time, and connects affected consumers with relevant attorneys via email — all cited, traceable, and live on the web.

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
Reddit API
    │
    ▼
Guild.ai (scheduled agent, timer-triggered)
    │  ingests posts on interval
    ▼
ClickHouse (raw + enriched tables, full provenance)
    │  analytics & trace queries
    ▼
Pioneer (fine-tuned ranker)
    │  legal relevance score + statute tagging
    ▼
Senso.ai (web-published agent output + cited.md)
    │  live citations, shareable URLs
    ▼
Composio → Gmail
    │  consumer alert + attorney lead email
    ▼
Render (deployed, always-on)
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
git clone https://github.com/<your-handle>/seconds-ai
cd seconds-ai
cp .env.example .env  # fill in API keys
pip install -r requirements.txt

# Run the full pipeline locally
python pipeline/run.py

# Deploy to Render
render deploy
```

### Required Environment Variables

```
REDDIT_CLIENT_ID=
REDDIT_CLIENT_SECRET=
CLICKHOUSE_HOST=
CLICKHOUSE_USER=
CLICKHOUSE_PASSWORD=
PIONEER_API_KEY=
COMPOSIO_API_KEY=
GMAIL_ACCOUNT=
SENSO_API_KEY=
GUILD_API_KEY=
LANGFUSE_PUBLIC_KEY=
LANGFUSE_SECRET_KEY=
```

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
