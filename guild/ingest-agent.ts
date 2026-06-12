"use agent"

/**
 * seconds.ai — autonomous ingestion agent (Guild.ai)
 *
 * SELF-CONTAINED: on each Trigger it
 *   1) searches Reddit + the open web via Firecrawl,
 *   2) enriches each result (company / complaint-type / legal-signal score), and
 *   3) writes leads straight into ClickHouse Cloud over its HTTP interface.
 *
 * Both endpoints (Firecrawl, ClickHouse :8443) are public, so the agent needs
 * NO other infrastructure — it is the whole ingestion pipeline, running
 * autonomously on Guild's schedule. That is the "acts on real-time data without
 * manual intervention" story.
 *
 * Config is passed via the Trigger --input (Guild has no CLI secret flag); use a
 * scoped ClickHouse user (guild_writer: INSERT on leads + ingest_runs only).
 *
 * Deploy: see guild/README.md
 */

import { agent, type Task } from "@guildai/agents-sdk"
import { z } from "zod"

const inputSchema = z.object({
  firecrawlKey: z.string().describe("Firecrawl API key (fc-...)"),
  chHost: z.string().describe("ClickHouse Cloud host, e.g. xxx.us-west-2.aws.clickhouse.cloud"),
  chUser: z.string().default("guild_writer"),
  chPassword: z.string().describe("ClickHouse password for chUser"),
  chDatabase: z.string().default("seconds"),
  queries: z.array(z.string()).optional(),
  limit: z.number().optional().describe("results per query (default 6)"),
  scrape: z.boolean().optional().describe("scrape full page markdown (slow). default false"),
})

const outputSchema = z.object({
  run_id: z.string(),
  posts_fetched: z.number(),
  leads_written: z.number(),
})

type Input = z.infer<typeof inputSchema>
type Output = z.infer<typeof outputSchema>

const DEFAULT_QUERIES = [
  "site:reddit.com overcharged hidden fee can't cancel refund denied",
  "site:reddit.com data breach leaked my personal information lawsuit",
  "site:reddit.com defective product injury recall class action",
  "consumer class action complaint deceptive advertising 2026",
  "subscription auto-renew kept charging debt collector harassment 2026",
]

// --- enrichment (ported from app/enrich.py; keep keys in sync) ---------------
const COMPLAINT_TYPES: [string, string[]][] = [
  ["data_breach", ["data breach", "leaked", "hacked", "exposed my", "stolen data", "ssn", "social security number", "identity theft"]],
  ["subscription_trap", ["can't cancel", "cant cancel", "kept charging", "auto-renew", "auto renew", "free trial", "recurring charge", "hidden fee"]],
  ["defective_product", ["broke after", "stopped working", "defective", "caught fire", "recall", "injury", "faulty", "malfunction"]],
  ["deceptive_advertising", ["false advertising", "misleading", "scam", "ripoff", "rip off", "rip-off", "bait and switch", "not as described"]],
  ["billing_dispute", ["overcharged", "double charged", "double-charged", "wrong amount", "refused refund", "won't refund", "no refund"]],
  ["debt_collection", ["debt collector", "collections agency", "fdcpa", "harassing calls"]],
  ["privacy_spam", ["sold my data", "without consent", "spam texts", "spam calls", "robocall", "tcpa", "unsolicited"]],
]
const LEGAL_SIGNALS = ["class action", "class-action", "lawsuit", "sue", "attorney", "lawyer", "settlement", "ftc", "cfpb", "violation", "illegal", "my rights", "small claims", "arbitration", "deceptive", "consumer protection"]
const KNOWN_BRANDS = ["comcast", "xfinity", "verizon", "at&t", "t-mobile", "spectrum", "wells fargo", "bank of america", "chase", "amazon", "paypal", "venmo", "ticketmaster", "equifax", "experian", "spirit airlines", "frontier airlines", "geico", "uber", "lyft", "doordash", "instacart", "planet fitness", "adobe", "norton", "mcafee", "audible", "robinhood", "coinbase"]

function enrich(title: string, body: string, url: string) {
  const text = `${title}\n\n${body}`.trim()
  const low = text.toLowerCase()
  const companies = new Set<string>()
  for (const b of KNOWN_BRANDS) if (low.includes(b)) companies.add(b)
  try {
    const host = new URL(url).hostname.replace(/^www\./, "")
    const tok = host.split(".")[0]
    if (tok.length > 2 && !host.includes("redd")) companies.add(tok)
  } catch {}
  let complaint_type = "other"
  const matched = new Set<string>()
  for (const [t, terms] of COMPLAINT_TYPES) {
    let hit = false
    for (const w of terms) if (low.includes(w)) { matched.add(w); hit = true }
    if (hit && complaint_type === "other") complaint_type = t
  }
  const legal = LEGAL_SIGNALS.filter((w) => low.includes(w))
  legal.forEach((w) => matched.add(w))
  const money = /\$\s?\d|\b\d[\d,]*\s?(?:dollars|usd|bucks)\b/i.test(text) ? 1 : 0
  let score = 0
  if (legal.length) score += 0.35
  if (complaint_type !== "other") score += 0.25
  if (money) score += 0.15
  if (companies.size) score += 0.10
  if (legal.length >= 2) score += 0.10
  score = Math.min(1, Math.round(score * 1000) / 1000)
  return { text, companies: [...companies], complaint_type, keywords: [...matched], money_mentioned: money, signal_score: score }
}

function deriveSource(url: string): string {
  try { const h = new URL(url).hostname; return /(^|\.)reddit\.com$/i.test(h) ? "reddit" : "web" } catch { return "web" }
}
function stableId(source: string, url: string): string {
  if (source === "reddit") { const m = url.match(/\/comments\/([a-z0-9]+)/i); if (m) return m[1] }
  let h = 0
  for (let i = 0; i < url.length; i++) h = (h * 31 + url.charCodeAt(i)) >>> 0
  return "w" + h.toString(16)
}
function subFromUrl(url: string): string { const m = url.match(/\/r\/([^/]+)/); return m ? m[1] : "" }
function chTime(d: Date): string { return d.toISOString().slice(0, 19).replace("T", " ") }

// --- Firecrawl ---------------------------------------------------------------
type FCItem = { url: string; title: string; body: string }
async function firecrawlSearch(key: string, query: string, limit: number, scrape: boolean): Promise<FCItem[]> {
  const body: Record<string, unknown> = { query, limit }
  if (scrape) body.scrapeOptions = { formats: ["markdown"] }
  const res = await fetch("https://api.firecrawl.dev/v1/search", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${key}` },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`firecrawl ${res.status}: ${await res.text()}`)
  const json = (await res.json()) as { data?: Array<{ url: string; title?: string; description?: string; markdown?: string }> }
  return (json.data ?? []).filter((d) => d.url).map((d) => ({
    url: d.url, title: d.title ?? "", body: (d.markdown ?? d.description ?? "").slice(0, 8000),
  }))
}

// --- ClickHouse HTTP insert (JSONEachRow) ------------------------------------
async function chInsert(host: string, user: string, pass: string, db: string, table: string, rows: object[]): Promise<void> {
  if (!rows.length) return
  const url = `https://${host}:8443/?query=${encodeURIComponent(`INSERT INTO ${db}.${table} FORMAT JSONEachRow`)}`
  const res = await fetch(url, {
    method: "POST",
    headers: { "X-ClickHouse-User": user, "X-ClickHouse-Key": pass, "Content-Type": "text/plain" },
    body: rows.map((r) => JSON.stringify(r)).join("\n"),
  })
  if (!res.ok) throw new Error(`clickhouse insert ${table} ${res.status}: ${await res.text()}`)
}

async function run(input: Input, task: Task<{}>): Promise<Output> {
  const queries = input.queries?.length ? input.queries : DEFAULT_QUERIES
  const limit = input.limit ?? 6
  const scrape = input.scrape ?? false
  const started = new Date()
  const runId = "guild-" + started.getTime().toString(36)
  const createdUtc = chTime(started)

  // 1) acquire
  const seen = new Set<string>()
  const items: FCItem[] = []
  for (const q of queries) {
    try {
      for (const it of await firecrawlSearch(input.firecrawlKey, q, limit, scrape)) {
        if (!seen.has(it.url)) { seen.add(it.url); items.push(it) }
      }
    } catch (e) { console.log(`firecrawl failed [${q}]: ${e}`) }
  }

  // 2) enrich -> lead rows
  const leadRows = items.map((it) => {
    const source = deriveSource(it.url)
    const f = enrich(it.title, it.body, it.url)
    return {
      lead_id: stableId(source, it.url),
      source,
      source_url: it.url,
      subreddit: source === "reddit" ? subFromUrl(it.url) : "",
      created_utc: createdUtc,
      author: "",
      title: it.title,
      body: it.body,
      text: f.text,
      companies: f.companies,
      complaint_type: f.complaint_type,
      keywords: f.keywords,
      money_mentioned: f.money_mentioned,
      signal_score: f.signal_score,
      status: "unranked",
      run_id: runId,
    }
  })

  // 3) write to ClickHouse Cloud (leads dedup by lead_id via ReplacingMergeTree)
  await chInsert(input.chHost, input.chUser, input.chPassword, input.chDatabase, "leads", leadRows)
  await chInsert(input.chHost, input.chUser, input.chPassword, input.chDatabase, "ingest_runs", [{
    run_id: runId, source: "guild-firecrawl", query: `${queries.length} queries`,
    started_at: createdUtc, finished_at: chTime(new Date()),
    posts_fetched: items.length, posts_new: leadRows.length, leads_created: leadRows.length,
    status: "ok", error: "",
  }])

  const out: Output = { run_id: runId, posts_fetched: items.length, leads_written: leadRows.length }
  try {
    // @ts-ignore — task.ui is environment-provided
    task.ui?.notify?.(`seconds.ai [${runId}]: +${leadRows.length} leads into ClickHouse`)
  } catch { console.log("done:", out) }
  return out
}

export default agent({
  description: "seconds.ai — autonomously scrapes Reddit + web via Firecrawl and writes leads to ClickHouse",
  inputSchema,
  outputSchema,
  tools: {},
  run,
})
