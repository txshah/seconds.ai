# seconds.ai — Consumer Lawsuit Intelligence for Law Firms

Law firms that specialize in consumer protection are fighting for their clients — but they're losing time. By the time a complaint surfaces, gets reviewed, and reaches the right attorney, the window has often closed. **seconds.ai** is a fully autonomous agent pipeline that finds those moments in real time, scores them, and puts actionable intelligence in front of the right firm — in seconds.

---

## Problem Statement

Every day, thousands of consumers post about being wronged: data breaches, deceptive subscriptions, defective products, predatory practices. Law firms that care about these clients can't monitor all of it manually. They miss leads. They miss clients who needed them.

The gap isn't expertise — it's time. Law firms are reactive because the tools they have make them reactive.

**seconds.ai** makes them proactive. The pipeline monitors public discourse continuously, identifies legally relevant complaints, and delivers structured, cited intelligence directly to subscribed firms — so they can act for their clients before anyone else does.

---

## How It Works

Guild.ai orchestrates three agents that run end-to-end without human intervention:

**1. Ingestion Agent** — Powered by Firecrawl, this agent continuously scrapes Reddit threads, forums, and consumer complaint boards. Every post is extracted, deduplicated, and stored in ClickHouse with a full provenance trail: source URL, timestamp, and run ID.

**2. Pioneer Agent** — Runs inference on our fine-tuned Pioneer model against everything in ClickHouse. It scores each complaint for legal relevance, tags applicable federal and state statutes, and flags patterns that indicate a viable case. Only high-confidence signals move forward.

**3. Notification Agent** — Composio triggers personalized outreach based on each firm's subscription preferences — practice area, jurisdiction, and case type. Subscribed firms receive a structured alert containing the complaint summary, Pioneer score, and a link to the full cited output. Senso saves the agent's reasoning and sources to `cite.md` so every finding remains auditable and shareable.

---

## Real-Time Infrastructure

To support continuous monitoring and instant notifications, **seconds.ai** is deployed entirely on **Render**.

Render hosts our real-time services and ensures the pipeline remains always-on and production-ready. It coordinates communication between our infrastructure components:

* **Render → ClickHouse:** Queries newly ingested consumer complaints and provides the Pioneer agent access to historical and real-time data for scoring.
* **Render → Composio:** Triggers automated outreach workflows whenever Pioneer identifies a qualifying legal signal.
* **Render → Agent Services:** Deploys and manages the ingestion, inference, and notification services powering the entire system.

By deploying on Render, we gain automatic scaling, simplified infrastructure management, and reliable execution of our end-to-end autonomous pipeline.

---

## Tool Usage

| Tool           | Role in Pipeline                                                                                                                |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------- |
| **Guild.ai**   | Orchestrates all three agents on a recurring schedule; manages lifecycle, retries, and agent handoffs                           |
| **Firecrawl**  | Powers the ingestion agent — scrapes Reddit, forums, and complaint boards and feeds structured data downstream                  |
| **ClickHouse** | Stores every ingested record with full provenance; Pioneer queries it for scoring; dashboards show pipeline health in real time |
| **Pioneer**    | Fine-tuned model that scores complaints by legal relevance, classifies statute type, and filters noise — the intelligence layer |
| **Composio**   | Connects the notification agent to Gmail; sends subscription-filtered alerts to law firms without any manual step               |
| **Senso.ai**   | Publishes the agent's reasoning and sources as a live, citable artifact — every finding links back to evidence via `cite.md`    |
| **Render**     | Hosts and deploys the real-time system, coordinating agent services and facilitating communication with ClickHouse and Composio |

---

## Senso + Composio: From Finding to Firm

When Pioneer flags a qualifying complaint, two things happen simultaneously:

* **Senso** writes the agent's full reasoning — source posts, statute tags, confidence score, and supporting evidence — to `cite.md` and publishes it as a live, linkable web artifact. The law firm receives a URL they can open, share, and file.
* **Composio** sends Gmail alerts to every subscribed firm whose practice area and jurisdiction match the finding. The email includes a plain-language summary, the Pioneer score, and the Senso citation link.

Together, they transform a raw consumer complaint into a structured, cited, attorney-ready lead — automatically.

---

## Autonomy

Once running, the pipeline requires zero manual intervention:

* Guild.ai fires all three agents on schedule and handles retries
* Firecrawl deduplicates re-crawled content before it reaches ClickHouse
* Pioneer scores and filters complaints without human review
* Render hosts and maintains the real-time services powering the system
* Render triggers queries to ClickHouse and coordinates notification workflows through Composio
* Composio sends alerts only when confidence exceeds the predefined threshold
* Senso publishes updated citation pages after every qualifying run

A human only steps in to tune Pioneer ranking thresholds or add new sources to the watch list.

---

## Judging Alignment

| Criterion (20% each)         | How seconds.ai addresses it                                                                                                                            |
| ---------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Idea**                     | Law firms already pay for leads — seconds.ai delivers real-time, cited intelligence about potential consumer harm before competitors can react         |
| **Technical Implementation** | Autonomous multi-agent architecture spanning ingestion → inference → notification, deployed as a real-time system on Render                            |
| **Tool Use**                 | Seven sponsor tools each fulfill a distinct responsibility within the pipeline, creating a cohesive end-to-end solution                                |
| **Presentation**             | 3-minute demo: a live consumer complaint gets ingested, scored by Pioneer, cited by Senso, and emailed to a subscribed law firm in real time           |
| **Autonomy**                 | Guild.ai orchestrates the agents while Render maintains continuous deployment and execution — enabling a fully automated workflow from ingest to alert |
