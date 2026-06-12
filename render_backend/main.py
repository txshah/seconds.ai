import os
import json
import datetime
from typing import Dict, Any, List, Optional

import requests
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel


app = FastAPI(title="seconds.ai Render Deployment Stub")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

LATEST_RESULTS: List[Dict[str, Any]] = []
LAST_CITED_MD = "# seconds.ai cited.md\n\nNo run has been executed yet.\n"


class ComplaintInput(BaseModel):
    text: str
    source_url: Optional[str] = None
    subreddit: Optional[str] = None
    company_hint: Optional[str] = None


def now_iso() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def mock_classifier(text: str, company_hint: Optional[str] = None) -> Dict[str, Any]:
    lowered = text.lower()

    score = 0.25
    issue_type = "general consumer complaint"
    possible_claim_type = "consumer protection signal"

    safety_terms = [
        "overheat",
        "overheated",
        "overheating",
        "fire",
        "smoke",
        "defective",
        "burn",
        "injury",
    ]

    privacy_terms = [
        "data breach",
        "privacy",
        "leaked",
        "stolen data",
        "personal information",
    ]

    billing_terms = [
        "keeps charging",
        "charged me",
        "unauthorized charge",
        "billing",
        "subscription",
        "cancelled",
        "canceled",
        "refund",
        "hidden fee",
    ]

    if any(k in lowered for k in safety_terms):
        score = 0.88
        issue_type = "product defect / safety"
        possible_claim_type = "product defect or safety complaint"
    elif any(k in lowered for k in privacy_terms):
        score = 0.84
        issue_type = "privacy / data security"
        possible_claim_type = "privacy or data breach complaint"
    elif any(k in lowered for k in billing_terms):
        score = 0.86
        issue_type = "billing / subscription cancellation"
        possible_claim_type = "deceptive billing or unfair subscription practice"

    company = company_hint or "Unknown Company"

    return {
        "company": company,
        "issue_type": issue_type,
        "risk_level": "high" if score >= 0.75 else "medium" if score >= 0.45 else "low",
        "risk_score": score,
        "possible_claim_type": possible_claim_type,
        "reasoning": "Mock classifier detected consumer-protection risk signals. Replace this with Pioneer output.",
        "recommended_action": "Review complaint cluster and notify internal demo recipient if above threshold.",
        "model_source": "mock_fallback",
    }


def call_pioneer_classifier(text: str, company_hint: Optional[str] = None) -> Dict[str, Any]:
    api_key = os.getenv("PIONEER_API_KEY")
    base_url = os.getenv("PIONEER_BASE_URL", "https://api.pioneer.ai/v1/chat/completions")
    model = os.getenv("PIONEER_MODEL", "LawClassActionClassifier")

    if not api_key:
        return mock_classifier(text, company_hint)

    prompt = f"""
Classify the following public consumer complaint for complaint escalation risk.

Return JSON only with:
company, issue_type, risk_level, risk_score, possible_claim_type, reasoning, recommended_action.

Do not provide legal advice. This is only operational complaint-risk triage.

Complaint:
{text}

Company hint:
{company_hint or "unknown"}
""".strip()

    try:
        response = requests.post(
            base_url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
            json={
                "model": model,
                "messages": [
                    {
                        "role": "system",
                        "content": "You are a consumer complaint risk classifier. Return structured JSON only.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                "stream": False,
            },
            timeout=45,
        )
        response.raise_for_status()
        raw = response.json()

        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "")
        try:
            parsed = json.loads(content)
            parsed["model_source"] = "pioneer"
            return parsed
        except Exception:
            return {
                "company": company_hint or "Unknown Company",
                "issue_type": "unknown",
                "risk_level": "medium",
                "risk_score": 0.50,
                "possible_claim_type": "unparsed Pioneer output",
                "reasoning": content,
                "recommended_action": "Review raw Pioneer output.",
                "model_source": "pioneer_raw",
            }

    except Exception as e:
        fallback = mock_classifier(text, company_hint)
        fallback["pioneer_error"] = str(e)
        return fallback


def generate_cited_md(results: List[Dict[str, Any]]) -> str:
    lines = [
        "# seconds.ai cited.md",
        "",
        f"Generated at: `{now_iso()}`",
        "",
        "## Safety Note",
        "",
        "This demo does not provide legal advice. It only detects public complaint-risk signals from synthetic or public-style demo data.",
        "",
        "## Classified Complaint Results",
        "",
    ]

    for idx, item in enumerate(results, start=1):
        lines.extend(
            [
                f"### {idx}. {item.get('company', 'Unknown Company')}",
                "",
                f"- Source: {item.get('source_url', 'demo/mock source')}",
                f"- Subreddit: {item.get('subreddit', 'demo')}",
                f"- Timestamp: {item.get('timestamp')}",
                f"- Issue type: {item.get('issue_type')}",
                f"- Risk level: **{item.get('risk_level')}**",
                f"- Risk score: `{item.get('risk_score')}`",
                f"- Possible claim type: {item.get('possible_claim_type')}",
                f"- Model source: {item.get('model_source')}",
                "",
                "**Reasoning:**",
                "",
                item.get("reasoning", "No reasoning provided."),
                "",
            ]
        )

    return "\n".join(lines)


@app.get("/", response_class=HTMLResponse)
def home():
    return """
<!doctype html>
<html>
<head>
  <title>seconds.ai Demo</title>
  <style>
    body { font-family: Arial, sans-serif; margin: 40px; max-width: 960px; }
    button { padding: 10px 16px; margin-right: 10px; cursor: pointer; }
    pre { background: #f5f5f5; padding: 16px; border-radius: 8px; overflow-x: auto; }
    .card { border: 1px solid #ddd; border-radius: 10px; padding: 18px; margin: 16px 0; }
  </style>
</head>
<body>
  <h1>seconds.ai</h1>
  <p>Consumer Complaint Risk Intelligence Agent — Render deployment stub.</p>

  <button onclick="runDemo()">Run Demo Pipeline</button>
  <button onclick="loadResults()">Load Latest Results</button>
  <button onclick="loadCitations()">Load cited.md</button>

  <div class="card">
    <h2>Output</h2>
    <pre id="output">Click "Run Demo Pipeline" to test the deployment.</pre>
  </div>

<script>
async function runDemo() {
  const res = await fetch('/run-demo', {method: 'POST'});
  const data = await res.json();
  document.getElementById('output').textContent = JSON.stringify(data, null, 2);
}
async function loadResults() {
  const res = await fetch('/latest-results');
  const data = await res.json();
  document.getElementById('output').textContent = JSON.stringify(data, null, 2);
}
async function loadCitations() {
  const res = await fetch('/cited.md');
  const text = await res.text();
  document.getElementById('output').textContent = text;
}
</script>
</body>
</html>
"""


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "seconds.ai render backend",
        "timestamp": now_iso(),
        "pioneer_configured": bool(os.getenv("PIONEER_API_KEY")),
        "composio_configured": bool(os.getenv("COMPOSIO_API_KEY")),
    }


@app.post("/classify")
def classify(input_data: ComplaintInput):
    result = call_pioneer_classifier(input_data.text, input_data.company_hint)
    result.update(
        {
            "text": input_data.text,
            "source_url": input_data.source_url or "demo/mock source",
            "subreddit": input_data.subreddit or "demo",
            "timestamp": now_iso(),
        }
    )
    return result


@app.post("/run-demo")
def run_demo():
    global LATEST_RESULTS, LAST_CITED_MD

    demo_posts = [
        {
            "text": "I cancelled my subscription two months ago but AcmeStream keeps charging me every month and support refuses to refund it.",
            "source_url": "https://reddit.com/r/personalfinance/demo_001",
            "subreddit": "personalfinance",
            "company_hint": "AcmeStream",
        },
        {
            "text": "My SmartCharge battery pack overheated while charging. Several people in the comments said the same model got extremely hot.",
            "source_url": "https://reddit.com/r/mildlyinfuriating/demo_002",
            "subreddit": "mildlyinfuriating",
            "company_hint": "SmartCharge",
        },
        {
            "text": "I bought a jacket online and the color looked different than the picture. Customer support gave me store credit.",
            "source_url": "https://reddit.com/r/shopping/demo_003",
            "subreddit": "shopping",
            "company_hint": "ShopCo",
        },
    ]

    results = []
    for post in demo_posts:
        result = call_pioneer_classifier(post["text"], post["company_hint"])
        result.update(
            {
                "text": post["text"],
                "source_url": post["source_url"],
                "subreddit": post["subreddit"],
                "timestamp": now_iso(),
            }
        )
        results.append(result)

    results = sorted(results, key=lambda x: float(x.get("risk_score", 0)), reverse=True)
    LATEST_RESULTS = results
    LAST_CITED_MD = generate_cited_md(results)

    high_risk = [r for r in results if float(r.get("risk_score", 0)) >= 0.75]

    return {
        "status": "demo_completed",
        "count": len(results),
        "high_risk_count": len(high_risk),
        "results": results,
        "next_steps": [
            "Replace demo_posts with crawler output.",
            "Replace mock fallback with finalized Pioneer model.",
            "Connect ClickHouse insert after classification.",
            "Connect Composio email action for high-risk results.",
        ],
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


@app.post("/send-alert")
def send_alert():
    return {
        "status": "stub",
        "message": "Composio email action will be connected here.",
        "composio_configured": bool(os.getenv("COMPOSIO_API_KEY")),
    }
