import os
import json
import datetime
from typing import Dict, Any, List, Optional

import requests
import clickhouse_connect
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel


app = FastAPI(title="seconds.ai Demo Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")

LATEST_RESULTS: List[Dict[str, Any]] = []
LAST_CITED_MD = "# seconds.ai cited.md\n\nNo run has been executed yet.\n"
LAST_SENSO_STATUS: Dict[str, Any] = {
    "status": "not_generated",
    "message": "Run the demo first to generate a citable evidence artifact.",
}



def clickhouse_configured() -> bool:
    required = [
        "CLICKHOUSE_HOST",
        "CLICKHOUSE_USER",
        "CLICKHOUSE_PASSWORD",
        "CLICKHOUSE_DATABASE",
    ]
    return all(os.getenv(k) for k in required)


def get_clickhouse_client():
    if not clickhouse_configured():
        raise RuntimeError("ClickHouse is not configured")

    return clickhouse_connect.get_client(
        host=os.getenv("CLICKHOUSE_HOST"),
        port=int(os.getenv("CLICKHOUSE_PORT", "8443")),
        username=os.getenv("CLICKHOUSE_USER"),
        password=os.getenv("CLICKHOUSE_PASSWORD"),
        database=os.getenv("CLICKHOUSE_DATABASE"),
        secure=True,
    )


def safe_table_name(name: str) -> bool:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_")
    return bool(name) and all(c in allowed for c in name)


def list_clickhouse_tables() -> list:
    client = get_clickhouse_client()
    result = client.query("SHOW TABLES")
    return [row[0] for row in result.result_rows]


def describe_clickhouse_table(table: str) -> list:
    if not safe_table_name(table):
        raise ValueError("Unsafe table name")

    client = get_clickhouse_client()
    result = client.query(f"DESCRIBE TABLE {table}")
    return [row[0] for row in result.result_rows]


def find_best_results_table() -> str:
    preferred = os.getenv("CLICKHOUSE_RESULTS_TABLE")
    if preferred:
        return preferred

    tables = list_clickhouse_tables()

    # Prefer names that sound like model outputs / inference results
    priority_terms = [
        "inference",
        "result",
        "pioneer",
        "prediction",
        "classified",
        "classification",
        "complaint",
        "dataset",
    ]

    scored = []
    for t in tables:
        name_score = sum(1 for term in priority_terms if term in t.lower())
        try:
            cols = describe_clickhouse_table(t)
            colset = set(cols)
            schema_score = sum(
                1 for c in [
                    "post_id",
                    "post",
                    "pioneer_score",
                    "pioneer_label",
                    "complaint_type",
                    "source_url",
                    "keywords",
                    "signal_score",
                ]
                if c in colset
            )
            scored.append((schema_score * 10 + name_score, t))
        except Exception:
            scored.append((name_score, t))

    scored.sort(reverse=True)

    if not scored or scored[0][0] <= 0:
        raise RuntimeError("Could not infer ClickHouse results table. Set CLICKHOUSE_RESULTS_TABLE.")

    return scored[0][1]


def normalize_clickhouse_value(v):
    if v is None:
        return None
    if isinstance(v, (list, tuple)):
        return [normalize_clickhouse_value(x) for x in v]
    return v


def parse_arrayish(v):
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, tuple):
        return [str(x).strip() for x in v if str(x).strip()]
    if isinstance(v, str):
        text = v.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(x).strip() for x in parsed if str(x).strip()]
        except Exception:
            pass
        return [x.strip() for x in text.replace("[", "").replace("]", "").replace("'", "").split(",") if x.strip()]
    return [str(v)]


def fetch_clickhouse_results(limit: int = 20) -> list:
    table = find_best_results_table()
    if not safe_table_name(table):
        raise ValueError("Unsafe table name")

    cols = describe_clickhouse_table(table)
    colset = set(cols)

    wanted = [
        "post_id",
        "post",
        "pioneer_score",
        "pioneer_label",
        "complaint_type",
        "taxonomy",
        "signal_score",
        "source_url",
        "companies",
        "keywords",
    ]
    selected = [c for c in wanted if c in colset]

    if not selected:
        raise RuntimeError(f"No expected columns found in ClickHouse table: {table}")

    order_col = "pioneer_score" if "pioneer_score" in colset else selected[0]

    query = f"""
        SELECT {", ".join(selected)}
        FROM {table}
        ORDER BY {order_col} DESC
        LIMIT {int(limit)}
    """

    client = get_clickhouse_client()
    result = client.query(query)

    rows = []
    for raw in result.result_rows:
        item = {}
        for idx, col in enumerate(selected):
            item[col] = normalize_clickhouse_value(raw[idx])
        item["_clickhouse_table"] = table
        rows.append(item)

    return rows



def count_clickhouse_rows(table: str) -> int:
    if not safe_table_name(table):
        raise ValueError("Unsafe table name")

    client = get_clickhouse_client()
    result = client.query(f"SELECT count() FROM {table}")
    return int(result.result_rows[0][0])


def enrich_clickhouse_row(item: Dict[str, Any]) -> Dict[str, Any]:
    post = str(item.get("post") or "")
    title = post.split("\n")[0][:160] if post else "Untitled signal"

    pioneer_score = float(item.get("pioneer_score") or 0)
    signal_score = float(item.get("signal_score") or 0)

    # If score is stored as 0-1, convert to percent for UI.
    risk_percent = pioneer_score * 100 if pioneer_score <= 1 else pioneer_score
    signal_percent = signal_score * 100 if signal_score <= 1 else signal_score

    label = str(item.get("pioneer_label") or "unclassified signal")
    complaint_type = str(item.get("complaint_type") or "unknown")
    source_url = str(item.get("source_url") or "")
    keywords = parse_arrayish(item.get("keywords"))
    companies = parse_arrayish(item.get("companies"))

    if not source_url:
        source_url = "No source URL provided"

    return {
        "post_id": str(item.get("post_id") or "unknown"),
        "company": companies[0] if companies else "Entity not extracted",
        "title": title,
        "text": post[:1200],
        "source_url": source_url,
        "source_type": get_source_type(source_url),
        "pioneer_score": pioneer_score,
        "risk_score": round(risk_percent, 1),
        "signal_score": round(signal_percent, 1),
        "risk_level": risk_level(pioneer_score if pioneer_score <= 1 else pioneer_score / 100),
        "pioneer_label": label,
        "complaint_type": complaint_type,
        "taxonomy": str(item.get("taxonomy") or ""),
        "keywords": keywords,
        "companies": companies,
        "reasoning": (
            f"Pioneer classified this source as '{label}' with "
            f"{round(risk_percent, 1)}% confidence."
            + (f" Keywords: {', '.join(keywords)}." if keywords else "")
        ),
        "plain_language_summary": f"Potential {label} signal detected.",
        "recommended_action": "Review source evidence and route to consumer-protection/legal intake team.",
        "model_source": "clickhouse_pioneer_result",
        "clickhouse_table": item.get("_clickhouse_table"),
        "timestamp": now_iso(),
    }



DEMO_EXAMPLES = [
    {
        "post_id": "94de17865b86dee3",
        "post": "Fair Debt Collection Practices Act Claims - McCarthy Law\n\nIf a collector has violated the FDCPA, you can sue the collector in court for certain types of damages. These damages can include monetary damages, attorney's ...",
        "pioneer_score": 1.0,
        "pioneer_label": "fdcpa violation",
        "complaint_type": "debt_collection",
        "taxonomy": "",
        "signal_score": 0.699999988079071,
        "source_url": "https://mccarthylawyer.com/collections-violations/fair-debt-collections-claims/",
        "companies": [],
        "keywords": ["attorney", "fdcpa", "sue "],
    },
    {
        "post_id": "e99a4c2a6ecaecd8",
        "post": "Product Liability Lawsuits 2026: Key Cases, Stats & How to Get Justice\n\nProduct liability lawsuits hold manufacturers, distributors, and sellers accountable when defective products cause injury or death. In ...",
        "pioneer_score": 0.9999997615814209,
        "pioneer_label": "product liability",
        "complaint_type": "defective_product",
        "taxonomy": "",
        "signal_score": 0.6000000238418579,
        "source_url": "https://samandashlaw.com/resources/product-liability-lawsuits-2026-key-cases-stats-how-to-get-justice/",
        "companies": [],
        "keywords": ["defective", "injury", "lawsuit"],
    },
    {
        "post_id": "pjvyeu",
        "post": "Product liability/defective product resulting in severe injury (advice)\n\nThe lawyer will evaluate how much capability/lifelong damage has been inflicted upon you (likely via several different doctors appointments). If ...",
        "pioneer_score": 0.9999995231628418,
        "pioneer_label": "product liability",
        "complaint_type": "defective_product",
        "taxonomy": "",
        "signal_score": 0.6000000238418579,
        "source_url": "https://www.reddit.com/r/legaladvicecanada/comments/pjvyeu/product_liabilitydefective_product_resulting_in/",
        "companies": [],
        "keywords": ["defective", "injury", "lawyer"],
    },
]


class ComplaintInput(BaseModel):
    text: str
    source_url: Optional[str] = None
    subreddit: Optional[str] = None
    company_hint: Optional[str] = None


def now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def get_source_type(url: str) -> str:
    if "reddit.com" in url:
        return "Reddit complaint thread"
    if "law" in url or "lawyer" in url:
        return "Legal reference source"
    return "Public web source"


def risk_level(score: float) -> str:
    if score >= 0.90:
        return "critical"
    if score >= 0.75:
        return "high"
    if score >= 0.45:
        return "medium"
    return "low"


def enrich_example(item: Dict[str, Any]) -> Dict[str, Any]:
    pioneer_score = float(item["pioneer_score"])
    signal_score = float(item["signal_score"])
    label = item["pioneer_label"]
    complaint_type = item["complaint_type"]
    source_url = item["source_url"]
    keywords = [k.strip() for k in item.get("keywords", [])]

    if complaint_type == "debt_collection":
        action = "Flag FDCPA-related signal and notify consumer-protection review team."
        plain = "Possible debt-collection rights signal detected."
    elif complaint_type == "defective_product":
        action = "Flag product-liability signal and route to product-safety/class-action review."
        plain = "Possible defective-product or injury-related legal signal detected."
    else:
        action = "Review this public complaint signal."
        plain = "Consumer complaint signal detected."

    return {
        "post_id": item["post_id"],
        "company": "Entity not extracted",
        "title": item["post"].split("\n")[0],
        "text": item["post"],
        "source_url": source_url,
        "source_type": get_source_type(source_url),
        "pioneer_score": pioneer_score,
        "risk_score": round(pioneer_score * 100, 1),
        "signal_score": round(signal_score * 100, 1),
        "risk_level": risk_level(pioneer_score),
        "pioneer_label": label,
        "complaint_type": complaint_type,
        "keywords": keywords,
        "reasoning": (
            f"Pioneer classified this source as '{label}' with "
            f"{round(pioneer_score * 100, 1)}% confidence. "
            f"Keywords supporting the signal: {', '.join(keywords)}."
        ),
        "plain_language_summary": plain,
        "recommended_action": action,
        "model_source": "cached_pioneer_result",
        "timestamp": now_iso(),
    }


def generate_cited_md(results: List[Dict[str, Any]]) -> str:
    lines = [
        "# seconds.ai — Citable Evidence Artifact",
        "",
        f"Generated at: `{now_iso()}`",
        "",
        "## Demo Safety Note",
        "",
        "seconds.ai does not provide legal advice. This demo identifies public consumer-complaint and legal-risk signals from crawled public data and cached Pioneer model outputs.",
        "",
        "## Pipeline Trace",
        "",
        "- Crawl / ingest: public web and Reddit-style sources",
        "- Rank: cached Pioneer classifier outputs",
        "- Store: Render-hosted backend demo state; ClickHouse integration slot",
        "- Cite: this generated evidence artifact",
        "- Act: Composio alert integration slot",
        "- Publish: Senso-ready evidence bundle",
        "",
        "## Ranked Signals",
        "",
    ]

    for i, r in enumerate(results, 1):
        lines.extend([
            f"### {i}. {r['pioneer_label'].title()}",
            "",
            f"- Post ID: `{r['post_id']}`",
            f"- Source type: {r['source_type']}",
            f"- Source URL: {r['source_url']}",
            f"- Complaint type: `{r['complaint_type']}`",
            f"- Pioneer confidence: `{r['risk_score']}%`",
            f"- Signal score: `{r['signal_score']}%`",
            f"- Risk level: **{r['risk_level'].upper()}**",
            f"- Keywords: {', '.join(r['keywords'])}",
            "",
            "**Evidence snippet:**",
            "",
            f"> {r['text'][:450]}",
            "",
            "**Agent reasoning:**",
            "",
            r["reasoning"],
            "",
            "**Recommended action:**",
            "",
            r["recommended_action"],
            "",
        ])

    return "\n".join(lines)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "seconds.ai polished demo backend",
        "timestamp": now_iso(),
        "demo_mode": True,
        "pioneer_mode": "cached_outputs",
        "senso_configured": bool(os.getenv("SENSO_API_KEY") and os.getenv("SENSO_INGEST_URL")),
        "composio_configured": bool(os.getenv("COMPOSIO_API_KEY")),
        "clickhouse_configured": clickhouse_configured(),
    }


@app.post("/run-demo")
def run_demo(limit: int = Query(20, ge=1, le=100)):
    global LATEST_RESULTS, LAST_CITED_MD, LAST_SENSO_STATUS

    mode = "cached_demo_examples"
    table_used = None
    clickhouse_error = None
    total_available = None

    effective_limit = int(limit)

    try:
        if clickhouse_configured():
            table_used = find_best_results_table()
            total_available = count_clickhouse_rows(table_used)

            raw_rows = fetch_clickhouse_results(limit=effective_limit)
            results = [enrich_clickhouse_row(x) for x in raw_rows]

            if results:
                mode = "clickhouse_pioneer_results"
                table_used = results[0].get("clickhouse_table") or table_used
            else:
                results = [enrich_example(x) for x in DEMO_EXAMPLES[:effective_limit]]
                total_available = len(DEMO_EXAMPLES)
        else:
            results = [enrich_example(x) for x in DEMO_EXAMPLES[:effective_limit]]
            total_available = len(DEMO_EXAMPLES)

    except Exception as e:
        clickhouse_error = str(e)
        results = [enrich_example(x) for x in DEMO_EXAMPLES[:effective_limit]]
        total_available = len(DEMO_EXAMPLES)

    results = sorted(results, key=lambda x: x["risk_score"], reverse=True)

    LATEST_RESULTS = results
    LAST_CITED_MD = generate_cited_md(results)

    LAST_SENSO_STATUS = {
        "status": "artifact_generated",
        "message": "Citable evidence bundle generated. Ready to publish to Senso.ai if API credentials are configured.",
        "artifact_url": "/senso-artifact",
        "cited_md_url": "/cited.md",
        "generated_at": now_iso(),
    }

    high_risk_count = sum(1 for r in results if r["risk_level"] in ["critical", "high"])
    avg_score = round(sum(float(r.get("risk_score", 0)) for r in results) / len(results), 1) if results else 0

    return {
        "status": "demo_completed",
        "mode": mode,
        "clickhouse_table": table_used,
        "clickhouse_error": clickhouse_error,
        "requested_limit": effective_limit,
        "count": len(results),
        "total_available": total_available,
        "high_risk_count": high_risk_count,
        "avg_pioneer_score": avg_score,
        "results": results,
        "senso": LAST_SENSO_STATUS,
    }

@app.get("/latest-results")
def latest_results():
    return {
        "count": len(LATEST_RESULTS),
        "results": LATEST_RESULTS,
    }


@app.get("/cited.md", response_class=PlainTextResponse)
def cited_md():
    return LAST_CITED_MD


@app.get("/senso-artifact", response_class=HTMLResponse)
def senso_artifact():
    html = LAST_CITED_MD.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return f"""
<!doctype html>
<html>
<head>
  <title>seconds.ai Evidence Artifact</title>
  <style>
    body {{
      background: #0b1020;
      color: #e8ecff;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      padding: 36px;
      line-height: 1.6;
    }}
    .wrap {{
      max-width: 980px;
      margin: auto;
      background: rgba(255,255,255,.06);
      border: 1px solid rgba(255,255,255,.12);
      border-radius: 24px;
      padding: 32px;
      box-shadow: 0 30px 90px rgba(0,0,0,.45);
    }}
    pre {{
      white-space: pre-wrap;
      word-break: break-word;
      font-size: 14px;
    }}
    .badge {{
      display: inline-block;
      padding: 8px 12px;
      background: rgba(53, 228, 255, .12);
      color: #79f0ff;
      border: 1px solid rgba(53, 228, 255, .35);
      border-radius: 999px;
      margin-bottom: 18px;
      font-weight: 700;
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="badge">Senso-ready citable artifact</div>
    <pre>{html}</pre>
  </div>
</body>
</html>
"""


@app.post("/publish-senso")
def publish_senso():
    global LAST_SENSO_STATUS

    api_key = os.getenv("SENSO_API_KEY")
    ingest_url = os.getenv("SENSO_INGEST_URL")

    if not api_key or not ingest_url:
        LAST_SENSO_STATUS = {
            "status": "local_artifact_only",
            "message": "Senso API credentials are not configured. Local citable artifact is available at /senso-artifact and /cited.md.",
            "artifact_url": "/senso-artifact",
            "cited_md_url": "/cited.md",
            "generated_at": now_iso(),
        }
        return LAST_SENSO_STATUS

    try:
        response = requests.post(
            ingest_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "title": "seconds.ai citable evidence artifact",
                "content": LAST_CITED_MD,
                "metadata": {
                    "project": "seconds.ai",
                    "generated_at": now_iso(),
                    "source": "Render demo backend",
                },
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()

        LAST_SENSO_STATUS = {
            "status": "published",
            "message": "Evidence artifact published to Senso.ai.",
            "senso_response": data,
            "generated_at": now_iso(),
        }
        return LAST_SENSO_STATUS

    except Exception as e:
        LAST_SENSO_STATUS = {
            "status": "publish_failed",
            "message": str(e),
            "fallback_artifact_url": "/senso-artifact",
            "cited_md_url": "/cited.md",
            "generated_at": now_iso(),
        }
        return LAST_SENSO_STATUS


@app.post("/send-alert")
def send_alert():
    return {
        "status": "demo_stub",
        "message": "Composio alert action placeholder. In final demo, this sends only to approved demo/team recipients.",
        "composio_configured": bool(os.getenv("COMPOSIO_API_KEY")),
        "clickhouse_configured": clickhouse_configured(),
    }


@app.get("/clickhouse/health")
def clickhouse_health():
    if not clickhouse_configured():
        return {
            "status": "not_configured",
            "message": "Set CLICKHOUSE_HOST, CLICKHOUSE_USER, CLICKHOUSE_PASSWORD, and CLICKHOUSE_DATABASE.",
        }

    try:
        client = get_clickhouse_client()
        version = client.query("SELECT version()").result_rows[0][0]
        tables = list_clickhouse_tables()
        best_table = None
        try:
            best_table = find_best_results_table()
        except Exception as e:
            best_table = f"not inferred: {e}"

        return {
            "status": "ok",
            "version": version,
            "database": os.getenv("CLICKHOUSE_DATABASE"),
            "table_count": len(tables),
            "tables": tables,
            "best_results_table": best_table,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }


@app.get("/clickhouse/tables")
def clickhouse_tables():
    try:
        tables = list_clickhouse_tables()
        out = []
        for t in tables:
            try:
                out.append({"table": t, "columns": describe_clickhouse_table(t)})
            except Exception as e:
                out.append({"table": t, "error": str(e)})
        return {"status": "ok", "tables": out}
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/clickhouse/preview")
def clickhouse_preview(table: Optional[str] = None, limit: int = 5):
    try:
        table = table or find_best_results_table()
        if not safe_table_name(table):
            return {"status": "error", "message": "Unsafe table name"}

        cols = describe_clickhouse_table(table)
        selected = cols[: min(len(cols), 12)]

        client = get_clickhouse_client()
        result = client.query(f"SELECT {', '.join(selected)} FROM {table} LIMIT {int(limit)}")

        rows = []
        for raw in result.result_rows:
            rows.append({col: normalize_clickhouse_value(raw[i]) for i, col in enumerate(selected)})

        return {
            "status": "ok",
            "table": table,
            "columns": cols,
            "preview": rows,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}



@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!doctype html>
<html>
<head>
  <title>seconds.ai — Legal Signal Intelligence</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {
      --bg: #f7f4ed;
      --paper: #fffdf8;
      --ink: #141414;
      --muted: #6f6a60;
      --line: #e4ddd1;
      --black: #111111;
      --green: #1f5d50;
      --gold: #9b7438;
      --red: #9f3b3b;
      --soft-red: #fff1ef;
      --soft-green: #edf7f3;
      --soft-gold: #fbf4e7;
      --shadow: 0 24px 70px rgba(20, 20, 20, .08);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      background:
        radial-gradient(circle at top right, rgba(155,116,56,.12), transparent 35%),
        linear-gradient(180deg, #fffdf8 0%, var(--bg) 100%);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      min-height: 100vh;
    }

    .shell {
      max-width: 1240px;
      margin: 0 auto;
      padding: 24px 24px 46px;
    }

    .nav {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 18px 0 28px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 11px;
      font-weight: 850;
      letter-spacing: -.04em;
      font-size: 23px;
    }

    .logo {
      width: 35px;
      height: 35px;
      border-radius: 9px;
      background: var(--black);
      color: #f8efe0;
      display: grid;
      place-items: center;
      font-weight: 950;
      box-shadow: 0 14px 30px rgba(0,0,0,.14);
    }

    .nav-right {
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }

    .status-pill {
      padding: 8px 11px;
      border: 1px solid rgba(31,93,80,.18);
      color: var(--green);
      background: rgba(31,93,80,.06);
      border-radius: 999px;
      font-size: 12px;
      font-weight: 850;
    }

    .hero {
      display: grid;
      grid-template-columns: 1.05fr .95fr;
      gap: 22px;
      align-items: stretch;
      margin-bottom: 18px;
    }

    .hero-card, .panel, .metric {
      background: rgba(255,253,248,.88);
      border: 1px solid var(--line);
      border-radius: 24px;
      box-shadow: var(--shadow);
    }

    .hero-card {
      padding: 34px;
    }

    .eyebrow {
      color: var(--green);
      font-weight: 850;
      font-size: 13px;
      margin-bottom: 18px;
    }

    h1 {
      font-family: Georgia, "Times New Roman", serif;
      font-weight: 500;
      letter-spacing: -.055em;
      line-height: .96;
      font-size: clamp(46px, 6vw, 78px);
      margin: 0;
      max-width: 780px;
    }

    .sub {
      margin: 20px 0 24px;
      color: var(--muted);
      font-size: 17px;
      line-height: 1.55;
      max-width: 680px;
    }

    .actions {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }

    button, a.btn {
      border: 0;
      background: var(--black);
      color: white;
      border-radius: 12px;
      padding: 13px 16px;
      font-weight: 850;
      font-size: 14px;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      gap: 8px;
      box-shadow: 0 16px 35px rgba(0,0,0,.13);
    }

    button.secondary, a.secondary {
      background: white;
      color: var(--black);
      border: 1px solid var(--line);
      box-shadow: none;
    }


    .limit-control {
      margin-top: 14px;
      display: flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
      font-weight: 750;
    }

    .limit-control select {
      border: 1px solid var(--line);
      background: white;
      color: var(--black);
      border-radius: 10px;
      padding: 8px 10px;
      font-weight: 800;
      outline: none;
    }

    .legal-note {
      margin-top: 18px;
      font-size: 12px;
      color: var(--muted);
      line-height: 1.45;
      max-width: 620px;
    }

    .brief-card {
      padding: 20px;
      display: grid;
      gap: 12px;
    }

    .brief-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      border-bottom: 1px solid var(--line);
      padding-bottom: 12px;
    }

    .brief-main {
      background: var(--black);
      color: #fff7ea;
      border-radius: 18px;
      padding: 22px;
      min-height: 160px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
    }

    .brief-main small {
      color: #c9bfae;
      text-transform: uppercase;
      letter-spacing: .08em;
      font-weight: 850;
      font-size: 11px;
    }

    .brief-main h2 {
      font-family: Georgia, "Times New Roman", serif;
      font-size: 30px;
      line-height: 1.05;
      letter-spacing: -.04em;
      margin: 10px 0 0;
      font-weight: 500;
    }

    .process {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 9px;
    }

    .process-step {
      background: white;
      border: 1px solid var(--line);
      border-radius: 15px;
      padding: 12px;
      min-height: 74px;
    }

    .process-step.done {
      border-color: rgba(31,93,80,.25);
      background: var(--soft-green);
    }

    .process-step b {
      display: block;
      font-size: 12px;
      margin-bottom: 5px;
    }

    .process-step span {
      color: var(--muted);
      font-size: 11px;
      line-height: 1.35;
    }

    .metrics {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 12px;
      margin-bottom: 18px;
    }

    .metric {
      padding: 18px;
      box-shadow: none;
    }

    .metric .value {
      font-size: 31px;
      letter-spacing: -.05em;
      font-weight: 900;
    }

    .metric .label {
      color: var(--muted);
      font-size: 12px;
      font-weight: 800;
      margin-top: 4px;
      text-transform: uppercase;
      letter-spacing: .04em;
    }

    .workspace {
      display: grid;
      grid-template-columns: .88fr 1.12fr .72fr;
      gap: 14px;
      align-items: start;
    }

    .panel {
      padding: 18px;
      box-shadow: none;
    }

    .panel-head {
      display: flex;
      justify-content: space-between;
      align-items: end;
      gap: 10px;
      margin-bottom: 14px;
    }

    .panel-head h3 {
      margin: 0;
      font-size: 17px;
      letter-spacing: -.03em;
    }

    .panel-head span {
      color: var(--muted);
      font-size: 12px;
      font-weight: 700;
    }

    .signals {
      display: grid;
      gap: 9px;
    }

    .signal {
      background: white;
      border: 1px solid var(--line);
      border-radius: 17px;
      padding: 13px;
      cursor: pointer;
      transition: all .16s ease;
    }

    .signal:hover, .signal.active {
      border-color: rgba(20,20,20,.42);
      transform: translateY(-1px);
      box-shadow: 0 12px 26px rgba(20,20,20,.06);
    }

    .signal-row {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
    }

    .signal h4 {
      margin: 0 0 5px;
      font-size: 14px;
      letter-spacing: -.02em;
      line-height: 1.25;
    }

    .signal p {
      margin: 0;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.38;
    }

    .risk {
      font-size: 10px;
      font-weight: 950;
      border-radius: 999px;
      padding: 5px 7px;
      text-transform: uppercase;
      white-space: nowrap;
    }

    .critical, .high {
      color: var(--red);
      background: var(--soft-red);
      border: 1px solid #f0c9c5;
    }

    .medium {
      color: var(--gold);
      background: var(--soft-gold);
      border: 1px solid #ead6a8;
    }

    .low {
      color: var(--green);
      background: var(--soft-green);
      border: 1px solid #c9e5dc;
    }

    .mini-meta {
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
      margin: 10px 0;
    }

    .tag {
      background: #f5f1e9;
      border: 1px solid #ece4d8;
      color: #665f53;
      border-radius: 999px;
      padding: 4px 7px;
      font-size: 10.5px;
      font-weight: 760;
    }

    .score-line {
      display: flex;
      justify-content: space-between;
      color: #4b463e;
      font-size: 11px;
      font-weight: 850;
      margin-bottom: 6px;
    }

    .bar {
      height: 7px;
      background: #eee8dc;
      border-radius: 999px;
      overflow: hidden;
    }

    .bar div {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, var(--green), var(--gold), var(--red));
      border-radius: 999px;
      transition: width .8s ease;
    }

    .evidence {
      background: white;
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 18px;
      min-height: 470px;
    }

    .evidence-title {
      display: flex;
      justify-content: space-between;
      gap: 12px;
      align-items: flex-start;
      margin-bottom: 12px;
    }

    .evidence-title h2 {
      margin: 0;
      font-family: Georgia, "Times New Roman", serif;
      font-weight: 500;
      letter-spacing: -.04em;
      font-size: 31px;
      line-height: 1.05;
    }

    .source-link {
      color: var(--black);
      text-decoration: none;
      border-bottom: 1px solid var(--black);
      font-size: 12px;
      font-weight: 850;
      white-space: nowrap;
    }

    .quote {
      background: #fbf8f2;
      border: 1px solid #eee5d7;
      border-left: 4px solid var(--black);
      border-radius: 14px;
      padding: 14px;
      color: #3e3932;
      font-size: 13px;
      line-height: 1.55;
      margin: 14px 0;
    }

    .brief-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 10px;
      margin-top: 14px;
    }

    .brief-box {
      border: 1px solid var(--line);
      background: #fffdf9;
      border-radius: 14px;
      padding: 13px;
      min-height: 104px;
    }

    .brief-box b {
      display: block;
      font-size: 12px;
      margin-bottom: 7px;
      color: var(--black);
    }

    .brief-box span {
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }

    .viz-stack {
      display: grid;
      gap: 12px;
    }

    .chart-card {
      background: white;
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 15px;
    }

    .chart-card h4 {
      margin: 0 0 12px;
      font-size: 13px;
      letter-spacing: -.02em;
    }

    .chart-row {
      display: grid;
      grid-template-columns: 78px 1fr 40px;
      gap: 8px;
      align-items: center;
      color: var(--muted);
      font-size: 11px;
      font-weight: 760;
      margin: 10px 0;
    }

    .chart-track {
      height: 8px;
      border-radius: 999px;
      background: #eee8dc;
      overflow: hidden;
    }

    .chart-track div {
      height: 100%;
      background: var(--black);
      border-radius: 999px;
    }

    .donut-wrap {
      display: grid;
      place-items: center;
      gap: 10px;
    }

    .donut {
      width: 122px;
      height: 122px;
      border-radius: 50%;
      background: conic-gradient(var(--red) 0 66%, var(--green) 66% 100%);
      position: relative;
    }

    .donut:after {
      content: "";
      position: absolute;
      inset: 20px;
      background: white;
      border-radius: 50%;
      border: 1px solid var(--line);
    }

    .donut-label {
      color: var(--muted);
      font-size: 12px;
      text-align: center;
      line-height: 1.4;
    }

    .sponsor-flow {
      margin-top: 16px;
      display: grid;
      grid-template-columns: repeat(6, 1fr);
      gap: 9px;
    }

    .tool {
      background: white;
      border: 1px solid var(--line);
      border-radius: 15px;
      padding: 12px;
      min-height: 86px;
    }

    .tool b {
      display: block;
      font-size: 12px;
      margin-bottom: 6px;
    }

    .tool span {
      color: var(--muted);
      font-size: 10.8px;
      line-height: 1.3;
    }

    .proof {
      margin-top: 16px;
      display: grid;
      grid-template-columns: .65fr 1.35fr;
      gap: 14px;
      align-items: center;
    }

    .proof-copy {
      padding: 4px;
    }

    .proof-copy h3 {
      font-family: Georgia, "Times New Roman", serif;
      font-weight: 500;
      letter-spacing: -.04em;
      font-size: 26px;
      margin: 0 0 8px;
    }

    .proof-copy p {
      color: var(--muted);
      font-size: 13px;
      line-height: 1.5;
      margin: 0;
    }

    .screenshot {
      background: white;
      border: 1px solid var(--line);
      border-radius: 20px;
      padding: 10px;
      overflow: hidden;
      box-shadow: 0 18px 45px rgba(20,20,20,.06);
    }

    .screenshot img {
      width: 100%;
      display: block;
      border-radius: 13px;
      border: 1px solid #eee8dc;
    }

    .footer {
      color: var(--muted);
      text-align: center;
      font-size: 12px;
      margin-top: 22px;
    }

    @media (max-width: 1100px) {
      .hero, .workspace, .proof { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, 1fr); }
      .sponsor-flow { grid-template-columns: repeat(2, 1fr); }
      .process { grid-template-columns: repeat(2, 1fr); }
      .nav-right span:not(.status-pill) { display: none; }
    }
  </style>
</head>

<body>
  <div class="shell">
    <nav class="nav">
      <div class="brand"><div class="logo">s</div>seconds.ai</div>
      <div class="nav-right">
        <span>Signals</span>
        <span>Evidence</span>
        <span>Workflow</span>
        <span class="status-pill">Demo mode · cached Pioneer</span>
      </div>
    </nav>

    <section class="hero">
      <div class="hero-card">
        <div class="eyebrow">Consumer protection intelligence</div>
        <h1>Legal signals, surfaced early.</h1>
        <p class="sub">
          A legal-style workspace for detecting high-confidence consumer complaint patterns,
          reviewing source evidence, and preparing attorney/admin alerts.
        </p>
        <div class="actions">
          <button onclick="runDemo()">Analyze Signals</button>
          <a class="btn secondary" href="/cited.md" target="_blank">Open cited.md</a>
          <button class="secondary" onclick="publishSenso()">Publish artifact</button>
          <button class="secondary" onclick="notifyUser()">Notify User</button>
        </div>

        <div class="limit-control">
          <span>Show</span>
          <select id="limitSelect" onchange="runDemo()">
            <option value="5">5</option>
            <option value="10" selected>10</option>
            <option value="20">20</option>
            <option value="50">50</option>
          </select>
          <span id="totalAvailable">of — total</span>
        </div>
        <div class="legal-note">
          Not legal advice. Demo uses cached Pioneer inference from the created dataset for fast presentation.
        </div>
      </div>

      <div class="brief-card">
        <div class="brief-top">
          <span>Autonomous legal-signal workflow</span>
          <span id="runStatus">Ready</span>
        </div>

        <div class="brief-main">
          <small>Current matter</small>
          <h2>Public complaint triage for consumer protection review.</h2>
        </div>

        <div class="process">
          <div class="process-step" id="step1"><b>Ingest</b><span>Crawled public evidence</span></div>
          <div class="process-step" id="step2"><b>Classify</b><span>Pioneer legal signal</span></div>
          <div class="process-step" id="step3"><b>Cite</b><span>Senso-ready artifact</span></div>
          <div class="process-step" id="step4"><b>Act</b><span>Composio alert packet</span></div>
        </div>
      </div>
    </section>

    <section class="metrics">
      <div class="metric"><div class="value" id="totalSignals">—</div><div class="label">Showing / total</div></div>
      <div class="metric"><div class="value" id="highRisk">—</div><div class="label">High confidence</div></div>
      <div class="metric"><div class="value" id="avgScore">—</div><div class="label">Avg Pioneer</div></div>
      <div class="metric"><div class="value">1</div><div class="label">Cited artifact</div></div>
    </section>

    <section class="workspace">
      <div class="panel">
        <div class="panel-head">
          <h3>Ranked signals</h3>
          <span>by confidence</span>
        </div>
        <div class="signals" id="signals"></div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>Evidence brief</h3>
          <span>source-grounded</span>
        </div>
        <div class="evidence" id="evidenceBox">
          Select a signal to review evidence.
        </div>
      </div>

      <div class="panel">
        <div class="panel-head">
          <h3>Visual summary</h3>
          <span>demo run</span>
        </div>

        <div class="viz-stack">
          <div class="chart-card">
            <h4>Pioneer confidence</h4>
            <div id="bars"></div>
          </div>

          <div class="chart-card">
            <h4>Risk mix</h4>
            <div class="donut-wrap">
              <div class="donut" id="donut"></div>
              <div class="donut-label" id="donutLabel">Run analysis to populate chart.</div>
            </div>
          </div>

          <div class="chart-card">
            <h4>Complaint type mix</h4>
            <div id="typeMix"></div>
          </div>
        </div>
      </div>
    </section>

    <section class="sponsor-flow">
      <div class="tool"><b>Render</b><span>Live dashboard and API deployment.</span></div>
      <div class="tool"><b>Pioneer</b><span>LawClassActionClassifier outputs.</span></div>
      <div class="tool"><b>Senso.ai</b><span>Citable evidence artifact layer.</span></div>
      <div class="tool"><b>ClickHouse</b><span>Provenance and analytics store.</span></div>
      <div class="tool"><b>Composio</b><span>Outbound alert action layer.</span></div>
      <div class="tool"><b>Guild.ai</b><span>Scheduled agent orchestration.</span></div>
    </section>

    <section class="panel proof">
      <div class="proof-copy">
        <div class="eyebrow">Pioneer proof</div>
        <h3>Model deployed as LawClassActionClassifier.</h3>
        <p>We use cached outputs in the video because live inference can take minutes, while still showing the promoted Pioneer adapter and recent inference traces.</p>
      </div>
      <div class="screenshot">
        <img src="/static/pioneer-deployment.png" alt="Pioneer deployment screenshot" />
      </div>
    </section>

    <div class="footer">
      Built at Harness Engineering Hack · Public-source intelligence · Cited evidence · Not legal advice
    </div>
  </div>

<script>
let results = [];
let selected = 0;

function pct(x) {
  return `${Number(x).toFixed(1)}%`;
}

function labelShort(label) {
  if (!label) return "";
  return label.length > 16 ? label.slice(0, 16) + "…" : label;
}

function markSteps(n) {
  for (let i = 1; i <= 4; i++) {
    document.getElementById(`step${i}`).classList.toggle("done", i <= n);
  }
}

async function runDemo() {
  document.getElementById("runStatus").textContent = "Analyzing";
  markSteps(0);

  for (let i = 1; i <= 4; i++) {
    await new Promise(r => setTimeout(r, 260));
    markSteps(i);
  }

  const limit = document.getElementById("limitSelect")?.value || "10";
  const res = await fetch(`/run-demo?limit=${limit}`, { method: "POST" });
  const data = await res.json();

  results = data.results || [];
  selected = 0;

  const total = data.total_available || data.count;
  document.getElementById("totalSignals").textContent = `${data.count}/${total}`;
  document.getElementById("totalAvailable").textContent = `of ${total} total`;
  document.getElementById("highRisk").textContent = data.high_risk_count;
  document.getElementById("avgScore").textContent = pct(data.avg_pioneer_score);
  document.getElementById("runStatus").textContent = "Complete";

  renderSignals();
  renderEvidence();
  renderBars();
  renderDonut();
  renderTypeMix();
}

function renderSignals() {
  const box = document.getElementById("signals");
  box.innerHTML = "";

  results.forEach((r, idx) => {
    const el = document.createElement("div");
    el.className = "signal" + (idx === selected ? " active" : "");
    el.onclick = () => {
      selected = idx;
      renderSignals();
      renderEvidence();
    };

    el.innerHTML = `
      <div class="signal-row">
        <div>
          <h4>${r.pioneer_label}</h4>
          <p>${r.title}</p>
        </div>
        <span class="risk ${r.risk_level}">${r.risk_level}</span>
      </div>

      <div class="mini-meta">
        <span class="tag">${r.complaint_type}</span>
        <span class="tag">${r.source_type}</span>
      </div>

      <div class="score-line"><span>Pioneer confidence</span><span>${r.risk_score}%</span></div>
      <div class="bar"><div style="width:${r.risk_score}%"></div></div>
    `;
    box.appendChild(el);
  });
}

function renderEvidence() {
  const box = document.getElementById("evidenceBox");

  if (!results.length) {
    box.innerHTML = "Run analysis to review evidence.";
    return;
  }

  const r = results[selected];

  box.innerHTML = `
    <div class="evidence-title">
      <h2>${r.pioneer_label}</h2>
      <a class="source-link" href="${r.source_url}" target="_blank">Open source</a>
    </div>

    <div class="mini-meta">
      ${(r.keywords || []).map(k => `<span class="tag">${k}</span>`).join("")}
      <span class="tag">${r.post_id}</span>
    </div>

    <div class="quote">${r.text}</div>

    <div class="brief-grid">
      <div class="brief-box">
        <b>Model finding</b>
        <span>${r.reasoning}</span>
      </div>
      <div class="brief-box">
        <b>Recommended routing</b>
        <span>${r.recommended_action}</span>
      </div>
      <div class="brief-box">
        <b>Citation status</b>
        <span>Included in generated cited.md and Senso-ready artifact.</span>
      </div>
      <div class="brief-box">
        <b>Action status</b>
        <span>Alert packet ready for approved demo recipient.</span>
      </div>
    </div>
  `;
}

function renderBars() {
  const box = document.getElementById("bars");
  box.innerHTML = "";

  results.forEach(r => {
    const row = document.createElement("div");
    row.className = "chart-row";
    row.innerHTML = `
      <span>${labelShort(r.pioneer_label)}</span>
      <div class="chart-track"><div style="width:${r.risk_score}%"></div></div>
      <span>${r.risk_score}%</span>
    `;
    box.appendChild(row);
  });
}

function renderDonut() {
  const high = results.filter(r => ["critical", "high"].includes(r.risk_level)).length;
  const other = results.length - high;
  const highPct = results.length ? Math.round(high / results.length * 100) : 0;

  document.getElementById("donut").style.background =
    `conic-gradient(var(--red) 0 ${highPct}%, var(--green) ${highPct}% 100%)`;

  document.getElementById("donutLabel").innerHTML =
    `<b>${high}</b> high-confidence · <b>${other}</b> lower-priority`;
}

function renderTypeMix() {
  const counts = {};
  results.forEach(r => counts[r.complaint_type] = (counts[r.complaint_type] || 0) + 1);

  const box = document.getElementById("typeMix");
  box.innerHTML = "";

  Object.entries(counts).forEach(([type, count]) => {
    const pctVal = Math.round((count / results.length) * 100);
    const row = document.createElement("div");
    row.className = "chart-row";
    row.innerHTML = `
      <span>${type.replace("_", " ")}</span>
      <div class="chart-track"><div style="width:${pctVal}%"></div></div>
      <span>${count}</span>
    `;
    box.appendChild(row);
  });
}

async function publishSenso() {
  const res = await fetch("/publish-senso", { method: "POST" });
  const data = await res.json();
  alert(`${data.status}: ${data.message}`);
}


function notifyUser() {
  const selectedSignal = results[selected] || null;

  if (!selectedSignal) {
    alert("Run analysis and select a signal first.");
    return;
  }

  alert(
    "Notification prepared for approved demo recipient.\n\n" +
    "Signal: " + (selectedSignal.pioneer_label || "N/A") + "\n" +
    "Confidence: " + (selectedSignal.risk_score || selectedSignal.pioneer_score || "N/A") + "%\n" +
    "Source: " + (selectedSignal.source_url || "N/A")
  );
}

window.onload = runDemo;
</script>
</body>
</html>
"""
