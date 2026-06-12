# Guild.ai — ingestion agent (Firecrawl → seconds.ai)

`ingest-agent.ts` is a Guild **coded agent**. On each Trigger it uses
**Firecrawl** to search Reddit + the open web for consumer-harm signals, then
`POST`s the results to the seconds.ai API (`/leads/ingest`), which enriches,
dedups, and stores them in ClickHouse.

> **Why Firecrawl?** Guild runs in the cloud, and Reddit blocks datacenter IPs.
> Firecrawl's managed proxy/anti-bot layer scrapes reliably from anywhere — and
> it searches the open web too, not just Reddit. The same pattern powers Guild's
> marketplace **Social Listening Bot**; we use the pattern but point the output
> at our pipeline (ClickHouse → Pioneer) instead of Slack.

> **Why so thin?** Guild sandboxes coded agents to the SDK, Zod, and `fetch`.
> Both Firecrawl and our API are plain HTTP, so that's all this needs. Storage,
> enrichment, dedup, and the Pioneer contract stay in the Python service.

## 1. Install the CLI + deps

```bash
npm i -g @guildai/cli        # or: npx @guildai/cli ...
cd guild && npm install
guild login
```

## 2. Connect the Firecrawl credential

```bash
guild credentials connect firecrawl     # provides the fc-... API key
```
The agent receives the key via its `firecrawlKey` input (wire it from this
connected credential when you create the trigger).

## 3. Deploy the agent

```bash
guild deploy ingest-agent.ts             # note the agent slug, e.g. `seconds-ingest`
```

## 4. Schedule it (hourly is Guild's minimum time-trigger frequency)

```bash
guild trigger create \
  --type time \
  --frequency HOURLY \
  --agent seconds-ingest \
  --input '{
    "apiBase":"https://<your-app>.onrender.com",
    "firecrawlKey":"fc-xxxxxxxx",
    "ingestToken":"<X-Ingest-Token if set>"
  }'
```

`--input` is delivered to the agent verbatim as its typed `Input`.

### Demo tip
For a live 3-minute demo (hourly is the minimum schedule), fire the agent
manually from the Guild UI, or attach a webhook trigger and `curl` it on stage.

## Manage

```bash
guild trigger list
guild trigger deactivate <id>            # pause without deleting
```

## How it maps to the pipeline

```
Firecrawl search (Reddit + web)
   → [{source, source_url, title, body}, ...]
   → POST {apiBase}/leads/ingest
   → ClickHouse leads  →  GET /leads / export.jsonl  →  Pioneer
```
