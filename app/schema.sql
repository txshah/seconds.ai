-- seconds.ai ClickHouse schema
-- Every row carries a full provenance chain so any finding is auditable:
--   source_url + subreddit + created_utc + run_id  ->  ingest_runs
-- Designed for ClickHouse Cloud or a local clickhouse-server (identical DDL).

CREATE DATABASE IF NOT EXISTS seconds;

-- 1) ingest_runs — one row per pipeline execution. The provenance root.
CREATE TABLE IF NOT EXISTS seconds.ingest_runs
(
    run_id        String,
    source        LowCardinality(String),          -- 'reddit'
    query         String,                          -- subreddits / listing scanned
    started_at    DateTime64(3, 'UTC'),
    finished_at   DateTime64(3, 'UTC'),
    posts_fetched UInt32,
    posts_new     UInt32,
    leads_created UInt32,
    status        LowCardinality(String),          -- 'ok' | 'error'
    error         String DEFAULT ''
)
ENGINE = MergeTree
ORDER BY (started_at, run_id);

-- 2) raw_posts — immutable raw ingest. Dedup on post_id via ReplacingMergeTree.
--    Keeps the full original JSON so nothing about a source post is ever lost.
CREATE TABLE IF NOT EXISTS seconds.raw_posts
(
    post_id       String,                          -- reddit id (natural key)
    source        LowCardinality(String),
    subreddit     LowCardinality(String),
    author        String,
    title         String,
    body          String,
    permalink     String,                          -- provenance: canonical source URL
    external_url  String,                          -- linked product/article, if any
    created_utc   DateTime('UTC'),
    score         Int32,
    num_comments  UInt32,
    run_id        String,
    ingested_at   DateTime64(3, 'UTC') DEFAULT now64(3),
    raw_json      String
)
ENGINE = ReplacingMergeTree(ingested_at)
ORDER BY (post_id);

-- 3) leads — enriched, ranking-ready handoff surface for Pioneer.
--    Pioneer reads `text` + entities + signal_score, then writes back
--    pioneer_score / pioneer_label via the API (a newer updated_at wins).
CREATE TABLE IF NOT EXISTS seconds.leads
(
    lead_id         String,                         -- = post_id
    source          LowCardinality(String),
    source_url      String,                         -- provenance click-through
    subreddit       LowCardinality(String),
    created_utc     DateTime('UTC'),
    author          String,
    title           String,
    body            String,
    text            String,                         -- title + body, the model input
    -- extracted entities (heuristic now, LLM-upgradable later)
    companies       Array(String),
    complaint_type  LowCardinality(String),
    keywords        Array(String),
    money_mentioned UInt8,                          -- 0/1 flag
    -- cheap heuristic pre-score (0..1) so the list is pre-sorted before Pioneer
    signal_score    Float32,
    -- ranking lifecycle
    status          LowCardinality(String),         -- 'unranked' | 'ranked' | 'sent'
    pioneer_score   Nullable(Float32),              -- written back by Pioneer
    pioneer_label   Nullable(String),               -- statute / class-action tag
    pioneer_model   Nullable(String),
    ranked_at       Nullable(DateTime64(3, 'UTC')),
    run_id          String,
    ingested_at     DateTime64(3, 'UTC') DEFAULT now64(3),
    updated_at      DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(updated_at)
ORDER BY (lead_id);

-- 4) rankings — PIONEER's WRITE surface. Pioneer INSERTs one row per scored post
--    (plain SQL, no app code). Newest ranked_at wins (ReplacingMergeTree), so
--    re-ranking is just another INSERT. All read surfaces JOIN this in.
CREATE TABLE IF NOT EXISTS seconds.rankings
(
    post_id       String,                          -- = leads.lead_id (Reddit id, no t3_ prefix)
    pioneer_score Float32,                          -- 0..1 relevance / qualification
    pioneer_label String DEFAULT '',               -- statute / class-action tag
    pioneer_model String DEFAULT '',               -- model id/version
    ranked_at     DateTime64(3, 'UTC') DEFAULT now64(3)
)
ENGINE = ReplacingMergeTree(ranked_at)
ORDER BY (post_id);

-- 5) posts — PIONEER's READ surface, shaped to the requested spec
--    (post_id / platform / taxonomy / user_id / post / metrics_json / raw_json)
--    plus our enrichment and the live ranking. A plain view, always fresh.
CREATE OR REPLACE VIEW seconds.posts AS
SELECT
    l.lead_id                                                    AS post_id,          -- stable join key
    if(l.source = 'reddit', concat('t3_', l.lead_id), l.lead_id) AS reddit_fullname,  -- t3_ form if needed
    l.source                                                    AS platform,
    l.subreddit                                                 AS taxonomy,
    l.author                                                    AS user_id,
    l.title                                                     AS title,
    l.text                                                      AS post,             -- title + body (model input)
    l.created_utc                                               AS created_utc,
    l.source_url                                                AS source_url,       -- provenance
    toJSONString(map('score', toInt64(r.score), 'num_comments', toInt64(r.num_comments))) AS metrics_json,
    r.raw_json                                                  AS raw_json,
    l.companies                                                 AS companies,
    l.complaint_type                                            AS complaint_type,
    l.keywords                                                  AS keywords,
    l.money_mentioned                                           AS money_mentioned,
    l.signal_score                                              AS signal_score,     -- our cheap pre-score
    if(rk.post_id = '', l.pioneer_score, rk.pioneer_score)      AS pioneer_score,    -- live ranking
    if(rk.post_id = '', l.pioneer_label, nullIf(rk.pioneer_label, '')) AS pioneer_label
FROM seconds.leads AS l FINAL
LEFT JOIN seconds.raw_posts AS r FINAL  ON r.post_id  = l.lead_id
LEFT JOIN seconds.rankings  AS rk FINAL ON rk.post_id = l.lead_id;

-- 6) case_signals — the PRODUCT view: leads rolled up into class-action
--    candidates by (company x complaint_type). Folds in Pioneer rankings, so a
--    score written to `rankings` flows straight into case_score. Leads with no
--    identifiable company (no defendant) drop out via arrayJoin on an empty array.
CREATE OR REPLACE VIEW seconds.case_signals AS
SELECT
    company,
    complaint_type,
    uniqExact(author)                                              AS complainants,
    count()                                                        AS mentions,
    uniqExactIf(author, created_utc >= now() - INTERVAL 7 DAY)     AS complainants_7d,
    uniqExactIf(author, created_utc >= now() - INTERVAL 30 DAY)    AS complainants_30d,
    round(avg(signal_score), 3)                                    AS avg_signal,
    round(avgOrNull(pioneer_score), 3)                             AS avg_pioneer_score,
    round(avg(money_mentioned), 3)                                 AS money_share,
    min(created_utc)                                               AS first_seen,
    max(created_utc)                                               AS last_seen,
    groupUniqArray(source)                                         AS sources,
    groupUniqArray(pioneer_label)                                  AS statutes,
    arraySlice(groupUniqArray(source_url), 1, 5)                   AS evidence,
    round(
        least(uniqExact(author) / 10.0, 1.0) * 0.5
      + avg(signal_score) * 0.3
      + least(uniqExactIf(author, created_utc >= now() - INTERVAL 7 DAY) / 5.0, 1.0) * 0.2
    , 3)                                                           AS case_score
FROM (
    SELECT
        arrayJoin(l.companies) AS company,
        l.author, l.complaint_type, l.signal_score, l.money_mentioned,
        l.created_utc, l.source, l.source_url,
        if(rk.post_id = '', l.pioneer_score, rk.pioneer_score) AS pioneer_score,
        if(rk.post_id = '', l.pioneer_label, nullIf(rk.pioneer_label, '')) AS pioneer_label
    FROM seconds.leads AS l FINAL
    LEFT JOIN seconds.rankings AS rk FINAL ON rk.post_id = l.lead_id
)
GROUP BY company, complaint_type;
