import os
import json
import datetime
from typing import Dict, Any, List, Optional

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel


app = FastAPI(title="seconds.ai Demo Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LATEST_RESULTS: List[Dict[str, Any]] = []
LAST_CITED_MD = "# seconds.ai cited.md\n\nNo run has been executed yet.\n"
LAST_SENSO_STATUS: Dict[str, Any] = {
    "status": "not_generated",
    "message": "Run the demo first to generate a citable evidence artifact.",
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
    }


@app.post("/run-demo")
def run_demo():
    global LATEST_RESULTS, LAST_CITED_MD, LAST_SENSO_STATUS

    results = [enrich_example(x) for x in DEMO_EXAMPLES]
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

    return {
        "status": "demo_completed",
        "mode": "cached_pioneer_outputs",
        "count": len(results),
        "high_risk_count": high_risk_count,
        "avg_pioneer_score": round(sum(r["risk_score"] for r in results) / len(results), 1),
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
    }


@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!doctype html>
<html>
<head>
  <title>seconds.ai — Consumer Protection Intelligence</title>
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <style>
    :root {
      --ink: #121417;
      --muted: #667085;
      --line: #e5e7eb;
      --soft: #f7f4ee;
      --soft2: #fbfaf7;
      --panel: #ffffff;
      --navy: #101828;
      --green: #236b5a;
      --gold: #b8892f;
      --red: #a04444;
      --blue: #344054;
      --shadow: 0 24px 70px rgba(16, 24, 40, .10);
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      background:
        radial-gradient(circle at top right, rgba(184, 137, 47, .12), transparent 34%),
        linear-gradient(180deg, #fbfaf7 0%, #f3efe7 100%);
      color: var(--ink);
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      min-height: 100vh;
    }

    .shell {
      max-width: 1220px;
      margin: 0 auto;
      padding: 26px 24px 50px;
    }

    .nav {
      height: 68px;
      display: flex;
      align-items: center;
      justify-content: space-between;
      border-bottom: 1px solid rgba(18,20,23,.08);
      margin-bottom: 42px;
    }

    .brand {
      display: flex;
      align-items: center;
      gap: 12px;
      font-weight: 800;
      letter-spacing: -.04em;
      font-size: 24px;
    }

    .mark {
      width: 36px;
      height: 36px;
      border-radius: 10px;
      background: #111827;
      position: relative;
      box-shadow: 0 12px 30px rgba(17,24,39,.18);
    }

    .mark:after {
      content: "s";
      position: absolute;
      color: #f8f3e7;
      font-weight: 900;
      font-size: 21px;
      left: 12px;
      top: 4px;
    }

    .navlinks {
      display: flex;
      gap: 24px;
      align-items: center;
      color: #475467;
      font-size: 14px;
      font-weight: 650;
    }

    .navlinks span {
      cursor: default;
    }

    .secure-pill {
      border: 1px solid rgba(35,107,90,.22);
      color: var(--green);
      background: rgba(35,107,90,.06);
      padding: 9px 13px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 800;
    }

    .hero {
      display: grid;
      grid-template-columns: 1.05fr .95fr;
      gap: 34px;
      align-items: center;
      margin-bottom: 34px;
    }

    .eyebrow {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      color: var(--green);
      font-size: 13px;
      font-weight: 850;
      background: rgba(35,107,90,.075);
      border: 1px solid rgba(35,107,90,.14);
      padding: 8px 11px;
      border-radius: 999px;
      margin-bottom: 18px;
    }

    .dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      background: var(--green);
      box-shadow: 0 0 0 4px rgba(35,107,90,.12);
    }

    h1 {
      margin: 0;
      font-size: clamp(44px, 6vw, 78px);
      line-height: .94;
      letter-spacing: -.075em;
      max-width: 760px;
    }

    .sub {
      margin: 20px 0 26px;
      max-width: 650px;
      color: #475467;
      font-size: 18px;
      line-height: 1.62;
    }

    .buttons {
      display: flex;
      gap: 12px;
      flex-wrap: wrap;
      align-items: center;
    }

    button, a.btn {
      appearance: none;
      border: 0;
      cursor: pointer;
      background: var(--navy);
      color: white;
      font-weight: 850;
      padding: 13px 17px;
      border-radius: 12px;
      text-decoration: none;
      box-shadow: 0 15px 40px rgba(16,24,40,.18);
      display: inline-flex;
      align-items: center;
      gap: 9px;
      font-size: 14px;
    }

    button.secondary, a.secondary {
      color: var(--navy);
      background: white;
      border: 1px solid var(--line);
      box-shadow: none;
    }

    .note {
      margin-top: 18px;
      color: #667085;
      font-size: 12px;
      line-height: 1.55;
      max-width: 640px;
    }

    .product {
      background: rgba(255,255,255,.72);
      border: 1px solid rgba(16,24,40,.10);
      border-radius: 26px;
      padding: 18px;
      box-shadow: var(--shadow);
      backdrop-filter: blur(18px);
    }

    .window-top {
      display: flex;
      justify-content: space-between;
      align-items: center;
      border-bottom: 1px solid var(--line);
      padding: 4px 4px 14px;
      margin-bottom: 16px;
    }

    .traffic {
      display: flex;
      gap: 6px;
    }

    .traffic i {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: #d0d5dd;
      display: block;
    }

    .matter-label {
      color: #667085;
      font-size: 12px;
      font-weight: 800;
    }

    .askbox {
      background: #111827;
      color: #f9fafb;
      border-radius: 20px;
      padding: 22px;
      margin-bottom: 14px;
    }

    .askbox small {
      color: #98a2b3;
      text-transform: uppercase;
      letter-spacing: .08em;
      font-weight: 850;
      font-size: 11px;
    }

    .askbox h3 {
      margin: 10px 0 14px;
      font-size: 22px;
      letter-spacing: -.04em;
    }

    .askbox .mini {
      color: #d0d5dd;
      line-height: 1.5;
      font-size: 14px;
    }

    .agent-steps {
      display: grid;
      gap: 10px;
    }

    .agent-step {
      display: flex;
      gap: 12px;
      align-items: flex-start;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 13px;
    }

    .agent-step.done .step-icon {
      background: rgba(35,107,90,.10);
      color: var(--green);
    }

    .step-icon {
      width: 28px;
      height: 28px;
      display: grid;
      place-items: center;
      border-radius: 9px;
      background: #f2f4f7;
      color: #667085;
      font-weight: 900;
      flex: none;
    }

    .agent-step b {
      font-size: 13px;
      display: block;
      margin-bottom: 3px;
    }

    .agent-step span {
      color: #667085;
      font-size: 12px;
      line-height: 1.4;
    }

    .metrics {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 14px;
      margin-bottom: 18px;
    }

    .metric {
      background: white;
      border: 1px solid rgba(16,24,40,.09);
      border-radius: 20px;
      padding: 18px;
      box-shadow: 0 16px 40px rgba(16,24,40,.06);
    }

    .metric .value {
      font-size: 30px;
      font-weight: 900;
      letter-spacing: -.05em;
      color: #111827;
    }

    .metric .label {
      margin-top: 5px;
      color: #667085;
      font-size: 12px;
      font-weight: 800;
      text-transform: uppercase;
      letter-spacing: .04em;
    }

    .main-grid {
      display: grid;
      grid-template-columns: .95fr 1.05fr;
      gap: 18px;
      align-items: start;
    }

    .panel {
      background: rgba(255,255,255,.82);
      border: 1px solid rgba(16,24,40,.10);
      border-radius: 24px;
      padding: 20px;
      box-shadow: 0 18px 54px rgba(16,24,40,.07);
      backdrop-filter: blur(12px);
    }

    .panel-title {
      display: flex;
      justify-content: space-between;
      align-items: flex-end;
      gap: 12px;
      margin-bottom: 15px;
    }

    .panel-title h2 {
      margin: 0;
      font-size: 19px;
      letter-spacing: -.04em;
    }

    .panel-title span {
      color: #667085;
      font-size: 12px;
      font-weight: 700;
    }

    .signals {
      display: grid;
      gap: 11px;
    }

    .signal {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 18px;
      padding: 15px;
      cursor: pointer;
      transition: all .16s ease;
    }

    .signal:hover {
      border-color: rgba(17,24,39,.28);
      transform: translateY(-1px);
      box-shadow: 0 16px 36px rgba(16,24,40,.06);
    }

    .signal.active {
      border-color: #111827;
      box-shadow: 0 0 0 2px rgba(17,24,39,.06);
    }

    .signal-head {
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: flex-start;
    }

    .signal h3 {
      margin: 0;
      font-size: 16px;
      letter-spacing: -.025em;
    }

    .risk {
      padding: 6px 8px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 950;
      text-transform: uppercase;
      white-space: nowrap;
    }

    .critical, .high {
      color: #9f1f1f;
      background: #fff1f1;
      border: 1px solid #f5caca;
    }

    .medium {
      color: #8a5a00;
      background: #fff8e5;
      border: 1px solid #f3dca3;
    }

    .low {
      color: #236b5a;
      background: #edf8f4;
      border: 1px solid #c9eadf;
    }

    .signal-summary {
      color: #667085;
      font-size: 13px;
      line-height: 1.45;
      margin: 8px 0 12px;
    }

    .chips {
      display: flex;
      gap: 7px;
      flex-wrap: wrap;
      margin-bottom: 12px;
    }

    .chip {
      background: #f2f4f7;
      border: 1px solid #e4e7ec;
      color: #475467;
      border-radius: 999px;
      padding: 5px 8px;
      font-size: 11px;
      font-weight: 750;
    }

    .score-row {
      display: flex;
      justify-content: space-between;
      color: #344054;
      font-weight: 850;
      font-size: 12px;
      margin-bottom: 7px;
    }

    .bar {
      height: 9px;
      background: #eef0f3;
      border-radius: 999px;
      overflow: hidden;
    }

    .bar div {
      height: 100%;
      width: 0%;
      background: linear-gradient(90deg, #236b5a, #b8892f, #a04444);
      transition: width .7s ease;
    }

    .evidence-card {
      border: 1px solid var(--line);
      background: #fff;
      border-radius: 20px;
      padding: 18px;
      margin-bottom: 14px;
    }

    .evidence-card h3 {
      margin: 0 0 8px;
      letter-spacing: -.035em;
      font-size: 22px;
    }

    .source {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 12px;
      color: #667085;
      font-size: 12px;
      margin-bottom: 14px;
    }

    .source a {
      color: #344054;
      font-weight: 800;
      text-decoration: none;
      border-bottom: 1px solid #98a2b3;
    }

    .quote {
      border-left: 3px solid #111827;
      padding: 12px 0 12px 14px;
      color: #344054;
      font-size: 14px;
      line-height: 1.55;
      background: #fbfaf7;
      border-radius: 0 12px 12px 0;
      margin-bottom: 14px;
    }

    .analysis {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
      margin-bottom: 14px;
    }

    .analysis div {
      background: #f9fafb;
      border: 1px solid #eef0f3;
      border-radius: 14px;
      padding: 13px;
    }

    .analysis b {
      display: block;
      font-size: 12px;
      margin-bottom: 5px;
      color: #344054;
    }

    .analysis span {
      color: #667085;
      line-height: 1.45;
      font-size: 13px;
    }

    .viz-grid {
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 14px;
    }

    .mini-chart {
      background: white;
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 15px;
    }

    .mini-chart h4 {
      margin: 0 0 12px;
      font-size: 14px;
      letter-spacing: -.02em;
    }

    .chart-row {
      display: grid;
      grid-template-columns: 92px 1fr 42px;
      gap: 8px;
      align-items: center;
      margin: 10px 0;
      font-size: 12px;
      color: #475467;
      font-weight: 750;
    }

    .chart-bar {
      height: 8px;
      background: #eef0f3;
      border-radius: 999px;
      overflow: hidden;
    }

    .chart-bar div {
      height: 100%;
      background: #111827;
      border-radius: 999px;
    }

    .donut {
      width: 112px;
      height: 112px;
      border-radius: 50%;
      background: conic-gradient(#a04444 0 66%, #236b5a 66% 100%);
      margin: 0 auto 12px;
      position: relative;
    }

    .donut:after {
      content: "";
      position: absolute;
      inset: 18px;
      border-radius: 50%;
      background: white;
      border: 1px solid var(--line);
    }

    .donut-label {
      text-align: center;
      color: #667085;
      font-size: 13px;
      line-height: 1.45;
    }

    .workflow {
      margin-top: 18px;
      display: grid;
      grid-template-columns: repeat(6, 1fr);
      gap: 10px;
    }

    .tool {
      background: white;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 12px;
      min-height: 102px;
    }

    .tool strong {
      display: block;
      color: #111827;
      font-size: 13px;
      margin-bottom: 6px;
    }

    .tool span {
      color: #667085;
      font-size: 11px;
      line-height: 1.35;
    }

    .tool.done {
      border-color: rgba(35,107,90,.24);
      background: linear-gradient(180deg, #fff, #f3faf7);
    }

    .footer {
      color: #667085;
      text-align: center;
      font-size: 12px;
      margin-top: 26px;
    }

    @media (max-width: 1000px) {
      .hero, .main-grid { grid-template-columns: 1fr; }
      .metrics { grid-template-columns: repeat(2, 1fr); }
      .workflow { grid-template-columns: repeat(2, 1fr); }
      .navlinks { display: none; }
    }
  </style>
</head>
<body>
  <div class="shell">
    <nav class="nav">
      <div class="brand"><div class="mark"></div>seconds.ai</div>
      <div class="navlinks">
        <span>Signals</span>
        <span>Evidence</span>
        <span>Workflow</span>
        <span>Deploy</span>
        <span class="secure-pill">Demo Mode · No Legal Advice</span>
      </div>
    </nav>

    <section class="hero">
      <div>
        <div class="eyebrow"><span class="dot"></span>Consumer protection intelligence for legal teams</div>
        <h1>Find actionable complaint signals before they become invisible.</h1>
        <p class="sub">
          seconds.ai turns crawled public complaints into ranked, citable, attorney-ready intelligence.
          The demo uses cached Pioneer outputs from our dataset for fast, reliable presentation.
        </p>
        <div class="buttons">
          <button onclick="runDemo()">Run Signal Analysis</button>
          <a class="btn secondary" href="/cited.md" target="_blank">Open cited.md</a>
          <button class="secondary" onclick="publishSenso()">Publish Evidence Artifact</button>
        </div>
        <div class="note">
          This demo does not give legal advice and does not contact real consumers. It shows complaint-signal triage,
          provenance, and alert readiness using public/crawled examples.
        </div>
      </div>

      <div class="product">
        <div class="window-top">
          <div class="traffic"><i></i><i></i><i></i></div>
          <div class="matter-label">Matter Intelligence · Live on Render</div>
        </div>

        <div class="askbox">
          <small>Agent request</small>
          <h3>Identify high-confidence public complaint signals with evidence and recommended routing.</h3>
          <div class="mini">
            Running cached Pioneer classification, generating citation trail, preparing Composio-ready alert packet.
          </div>
        </div>

        <div class="agent-steps">
          <div class="agent-step" id="step1"><div class="step-icon">1</div><div><b>Ingest public evidence</b><span>Use crawled public sources and source URLs.</span></div></div>
          <div class="agent-step" id="step2"><div class="step-icon">2</div><div><b>Classify legal signal</b><span>Pioneer label, confidence, complaint type.</span></div></div>
          <div class="agent-step" id="step3"><div class="step-icon">3</div><div><b>Generate cited artifact</b><span>Senso-ready evidence bundle and cited.md.</span></div></div>
          <div class="agent-step" id="step4"><div class="step-icon">4</div><div><b>Prepare outreach</b><span>Composio-ready admin or attorney lead alert.</span></div></div>
        </div>
      </div>
    </section>

    <section class="metrics">
      <div class="metric"><div class="value" id="totalSignals">3</div><div class="label">Crawled examples</div></div>
      <div class="metric"><div class="value" id="highRisk">—</div><div class="label">High-confidence signals</div></div>
      <div class="metric"><div class="value" id="avgScore">—</div><div class="label">Avg Pioneer confidence</div></div>
      <div class="metric"><div class="value">0</div><div class="label">Manual steps after deploy</div></div>
    </section>

    <section class="main-grid">
      <div class="panel">
        <div class="panel-title">
          <h2>Ranked Signals</h2>
          <span id="runStatus">Ready</span>
        </div>
        <div id="signals" class="signals"></div>
      </div>

      <div class="panel">
        <div class="panel-title">
          <h2>Evidence Workspace</h2>
          <span>Senso-ready citations</span>
        </div>

        <div class="evidence-card" id="evidenceBox">
          Select a signal to review source evidence and routing recommendation.
        </div>

        <div class="viz-grid">
          <div class="mini-chart">
            <h4>Confidence by Signal</h4>
            <div id="bars"></div>
          </div>
          <div class="mini-chart">
            <h4>Risk Mix</h4>
            <div class="donut" id="donut"></div>
            <div class="donut-label" id="donutLabel">Run analysis to populate chart.</div>
          </div>
        </div>
      </div>
    </section>

    <section class="workflow">
      <div class="tool done"><strong>Render</strong><span>Hosts live API, dashboard, cited.md, and demo endpoints.</span></div>
      <div class="tool done"><strong>Pioneer</strong><span>Cached legal signal classifier outputs from our dataset.</span></div>
      <div class="tool done"><strong>Senso.ai</strong><span>Citable evidence artifact layer for transparent reasoning.</span></div>
      <div class="tool done"><strong>ClickHouse</strong><span>Designed for provenance, scores, and analytics storage.</span></div>
      <div class="tool done"><strong>Composio</strong><span>Alert action layer for approved demo recipients.</span></div>
      <div class="tool done"><strong>Guild.ai</strong><span>Agent orchestration and scheduled workflow framing.</span></div>
    </section>

    <div class="footer">
      Built at Harness Engineering Hack · Public-source complaint intelligence · Cited evidence · Not legal advice
    </div>
  </div>

<script>
let results = [];
let selected = 0;

function pct(x) {
  return `${Number(x).toFixed(1)}%`;
}

function markSteps(n) {
  for (let i = 1; i <= 4; i++) {
    document.getElementById(`step${i}`).classList.toggle("done", i <= n);
  }
}

async function runDemo() {
  document.getElementById("runStatus").textContent = "Analyzing...";
  markSteps(0);

  for (let i = 1; i <= 4; i++) {
    await new Promise(r => setTimeout(r, 320));
    markSteps(i);
  }

  const res = await fetch("/run-demo", { method: "POST" });
  const data = await res.json();

  results = data.results || [];
  selected = 0;

  document.getElementById("totalSignals").textContent = data.count;
  document.getElementById("highRisk").textContent = data.high_risk_count;
  document.getElementById("avgScore").textContent = pct(data.avg_pioneer_score);
  document.getElementById("runStatus").textContent = "Analysis complete · cached Pioneer inference";

  renderSignals();
  renderEvidence();
  renderBars();
  renderDonut();
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
      <div class="signal-head">
        <div>
          <h3>${r.pioneer_label}</h3>
          <div class="signal-summary">${r.title}</div>
        </div>
        <span class="risk ${r.risk_level}">${r.risk_level}</span>
      </div>
      <div class="chips">
        <span class="chip">${r.complaint_type}</span>
        <span class="chip">${r.source_type}</span>
        <span class="chip">signal ${r.signal_score}%</span>
      </div>
      <div class="score-row"><span>Pioneer confidence</span><span>${r.risk_score}%</span></div>
      <div class="bar"><div style="width:${r.risk_score}%"></div></div>
    `;
    box.appendChild(el);
  });
}

function renderEvidence() {
  const box = document.getElementById("evidenceBox");

  if (!results.length) {
    box.innerHTML = "Run the signal analysis to review evidence.";
    return;
  }

  const r = results[selected];

  box.innerHTML = `
    <h3>${r.pioneer_label}</h3>
    <div class="source">
      <span>${r.source_type} · post id ${r.post_id}</span>
      <a href="${r.source_url}" target="_blank">Open source</a>
    </div>

    <div class="quote">${r.text}</div>

    <div class="chips">
      ${(r.keywords || []).map(k => `<span class="chip">${k}</span>`).join("")}
    </div>

    <div class="analysis">
      <div>
        <b>Model reasoning</b>
        <span>${r.reasoning}</span>
      </div>
      <div>
        <b>Recommended routing</b>
        <span>${r.recommended_action}</span>
      </div>
    </div>

    <div class="analysis">
      <div>
        <b>Citation status</b>
        <span>Included in generated cited.md and Senso-ready artifact.</span>
      </div>
      <div>
        <b>Action status</b>
        <span>Composio-ready alert packet prepared for approved demo recipient.</span>
      </div>
    </div>
  `;
}

function renderBars() {
  const box = document.getElementById("bars");
  box.innerHTML = "";

  results.forEach(r => {
    const shortLabel = r.pioneer_label.length > 20 ? r.pioneer_label.slice(0, 20) + "…" : r.pioneer_label;
    const row = document.createElement("div");
    row.className = "chart-row";
    row.innerHTML = `
      <span>${shortLabel}</span>
      <div class="chart-bar"><div style="width:${r.risk_score}%"></div></div>
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
    `conic-gradient(#a04444 0 ${highPct}%, #236b5a ${highPct}% 100%)`;
  document.getElementById("donutLabel").innerHTML =
    `<b>${high}</b> high-confidence signals · <b>${other}</b> lower-priority signals`;
}

async function publishSenso() {
  const res = await fetch("/publish-senso", { method: "POST" });
  const data = await res.json();
  alert(`${data.status}: ${data.message}`);
}

window.onload = runDemo;
</script>
</body>
</html>
"""
