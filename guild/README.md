# Guild.ai — autonomous ingestion agent

`ingest-agent.ts` is a **self-contained** Guild coded agent. On each Trigger it
searches Reddit + the open web via **Firecrawl**, enriches each result, and writes
leads straight into **ClickHouse Cloud** over its HTTP interface. Both endpoints
are public, so the agent needs no other infrastructure — it *is* the ingestion
pipeline, running autonomously on Guild's schedule. (That's the "acts on real-time
data without manual intervention" autonomy story.)

## 0. One-time — a scoped ClickHouse writer
Run as the admin/`default` user (ClickHouse Cloud SQL console, or any client):
```sql
CREATE USER guild_writer IDENTIFIED BY '<choose-a-password>';
GRANT INSERT ON seconds.leads      TO guild_writer;
GRANT INSERT ON seconds.ingest_runs TO guild_writer;
```

## 1. Install + authenticate (only you can do this)
```bash
npm i @guildai/cli -g
guild auth login          # browser login to YOUR Guild account
guild auth status
guild workspace select
```

## 2. Scaffold, then drop in our agent
```bash
cd guild
guild agent init --name seconds-ingest     # choose the coded / TypeScript template
# replace the generated agent entry file's contents with ingest-agent.ts (this file's sibling)
```

## 3. Test locally
```bash
guild agent test          # provide the input JSON below when prompted
```

## 4. Publish
```bash
guild agent save --message "seconds.ai autonomous ingest" --wait --publish
```

## 5. Schedule it — hourly Trigger (web UI or CLI)
```bash
guild trigger create \
  --type time --frequency HOURLY --agent seconds-ingest \
  --input '{
    "firecrawlKey": "fc-...",
    "chHost": "tqof521f2x.us-west-2.aws.clickhouse.cloud",
    "chUser": "guild_writer",
    "chPassword": "<guild_writer password>",
    "chDatabase": "seconds"
  }'
```

Hourly is Guild's minimum schedule — for a live demo, fire the agent manually
from the Guild web UI (or attach a webhook trigger). Each run + its output appears
in the Guild UI; new rows land in `seconds.leads` and `seconds.ingest_runs`.

## Notes
- Config goes through the Trigger `--input` because Guild manages service
  credentials in its web UI, not via CLI flags. Use the scoped `guild_writer`
  creds (not admin).
- Default is search-only (fast). Pass `"scrape": true` for full-page markdown (slow).
- The Python pipeline (`app/`) stays as the local-dev / backup path.
