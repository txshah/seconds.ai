"use agent"

/**
 * seconds.ai — ingestion agent (Guild.ai + Firecrawl)
 *
 * On each scheduled Trigger this agent:
 *   1) uses Firecrawl to search Reddit + the open web for consumer-harm signals
 *      (Firecrawl's proxy/anti-bot infra means no datacenter-IP blocking), then
 *   2) hands the results to the seconds.ai API (`POST /leads/ingest`), which
 *      enriches, dedups, and stores them in ClickHouse — the single writer.
 *
 * The split is deliberate: Guild+Firecrawl own *acquisition* (the agentic,
 * real-time part), the Python service owns *storage + the Pioneer contract*.
 * Coded agents are sandboxed to the SDK, Zod, and `fetch`, which is all this
 * needs — both Firecrawl and our API are plain HTTP.
 *
 * Deploy + schedule + credential wiring: see guild/README.md
 */

import { agent, type Task } from "@guildai/agents-sdk"
import { z } from "zod"

// Consumer-protection signal queries. Tune freely — this is the watch list.
const DEFAULT_QUERIES = [
  "site:reddit.com overcharged hidden fee can't cancel refund denied",
  "site:reddit.com data breach leaked my personal information lawsuit",
  "site:reddit.com defective product injury recall class action",
  "consumer class action complaint deceptive advertising 2026",
  "subscription trap auto-renew kept charging debt collector harassment",
]

const inputSchema = z.object({
  apiBase: z.string().describe("Base URL of the seconds.ai ingestion API (Render)"),
  firecrawlKey: z.string().describe("Firecrawl API key (fc-...). Wire from the Guild-connected Firecrawl credential."),
  ingestToken: z.string().optional().describe("X-Ingest-Token shared secret, if the API requires one"),
  queries: z.array(z.string()).optional().describe("Override the default search watch list"),
  limit: z.number().optional().describe("Results per query (default 5)"),
  scrape: z.boolean().optional().describe("Scrape full page markdown (richer, slow). Default false = fast snippets."),
})

const outputSchema = z.object({
  run_id: z.string(),
  status: z.string(),
  posts_fetched: z.number(),
  posts_new: z.number(),
  leads_created: z.number(),
  duration_s: z.number(),
})

type Input = z.infer<typeof inputSchema>
type Output = z.infer<typeof outputSchema>
type Item = { source: string; source_url: string; title: string; body: string }

function deriveSource(url: string): string {
  return /(^|\.)reddit\.com/i.test(url) ? "reddit" : "web"
}

async function firecrawlSearch(key: string, query: string, limit: number, scrape: boolean): Promise<Item[]> {
  const body: Record<string, unknown> = { query, limit }
  if (scrape) body.scrapeOptions = { formats: ["markdown"] } // richer but slow
  const res = await fetch("https://api.firecrawl.dev/v1/search", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`firecrawl ${res.status}: ${await res.text()}`)
  const json = (await res.json()) as {
    data?: Array<{ url: string; title?: string; description?: string; markdown?: string }>
  }
  return (json.data ?? [])
    .filter((d) => d.url)
    .map((d) => ({
      source: deriveSource(d.url),
      source_url: d.url,
      title: d.title ?? "",
      body: (d.markdown ?? d.description ?? "").slice(0, 8000),
    }))
}

async function run(input: Input, task: Task<{}>): Promise<Output> {
  const queries = input.queries?.length ? input.queries : DEFAULT_QUERIES
  const limit = input.limit ?? 5
  const scrape = input.scrape ?? false

  // 1) Acquire — Guild + Firecrawl search Reddit + the open web.
  const seen = new Set<string>()
  const items: Item[] = []
  for (const q of queries) {
    try {
      for (const it of await firecrawlSearch(input.firecrawlKey, q, limit, scrape)) {
        if (!seen.has(it.source_url)) {
          seen.add(it.source_url)
          items.push(it)
        }
      }
    } catch (e) {
      console.log(`firecrawl query failed [${q}]: ${e}`)
    }
  }

  // 2) Hand off — the Python service enriches, dedups, and stores.
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  if (input.ingestToken) headers["X-Ingest-Token"] = input.ingestToken
  const res = await fetch(`${input.apiBase.replace(/\/$/, "")}/leads/ingest`, {
    method: "POST",
    headers,
    body: JSON.stringify({ items, source_label: "firecrawl" }),
  })
  if (!res.ok) throw new Error(`ingest failed: ${res.status} ${await res.text()}`)
  const data = (await res.json()) as Output

  try {
    // @ts-ignore — task.ui is environment-provided
    task.ui?.notify?.(
      `seconds.ai: scanned ${items.length} results → +${data.leads_created} leads ` +
        `(${data.posts_new} new)`,
    )
  } catch {
    console.log("ingest result:", data)
  }
  return data
}

export default agent({
  description:
    "seconds.ai — scrapes Reddit + the open web via Firecrawl and feeds the lead pipeline",
  inputSchema,
  outputSchema,
  tools: {},
  run,
})
