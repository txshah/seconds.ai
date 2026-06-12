"""Heuristic enrichment: extract entities + a cheap legal-signal pre-score.

Deterministic and dependency-free on purpose — it runs anywhere and gives every
lead a complete set of ranking features the moment it's ingested. Pioneer later
overwrites `pioneer_score` with a learned ranking; `signal_score` just lets us
pre-sort the queue so the most promising leads surface first.

To upgrade: replace `enrich()` with an LLM extraction call. The output keys
(companies / complaint_type / keywords / money_mentioned / signal_score) are the
contract the schema and API depend on — keep them and nothing else changes.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?|\b\d[\d,]*\s?(?:dollars|usd|bucks)\b", re.I)

# Ordered: the first matching category wins as the primary complaint_type.
COMPLAINT_TYPES: dict[str, list[str]] = {
    "data_breach": ["data breach", "leaked", "hacked", "exposed my", "stolen data",
                    "ssn", "social security number", "identity theft"],
    "subscription_trap": ["can't cancel", "cant cancel", "kept charging", "auto-renew",
                          "auto renew", "free trial", "recurring charge", "hidden fee",
                          "dark pattern"],
    "defective_product": ["broke after", "stopped working", "defective", "caught fire",
                         "recall", "injury", "faulty", "malfunction"],
    "deceptive_advertising": ["false advertising", "misleading", "scam", "ripoff",
                            "rip off", "rip-off", "bait and switch", "not as described",
                            "fake reviews"],
    "billing_dispute": ["overcharged", "double charged", "double-charged", "wrong amount",
                       "refused refund", "won't refund", "wont refund", "no refund"],
    "debt_collection": ["debt collector", "collections agency", "fdcpa", "harassing calls"],
    "privacy_spam": ["sold my data", "without consent", "spam texts", "spam calls",
                    "robocall", "tcpa", "unsolicited"],
}

LEGAL_SIGNALS = ["class action", "class-action", "lawsuit", "sue them", "sue ", "attorney",
                 "lawyer", "settlement", "ftc", "cfpb", "fcc complaint", "violation",
                 "illegal", "my rights", "small claims", "arbitration", "deceptive",
                 "consumer protection"]

KNOWN_BRANDS = ["comcast", "xfinity", "verizon", "at&t", "t-mobile", "spectrum",
                "wells fargo", "bank of america", "chase", "amazon", "paypal", "venmo",
                "ticketmaster", "equifax", "experian", "transunion", "spirit airlines",
                "frontier airlines", "geico", "progressive", "uber", "lyft", "doordash",
                "instacart", "planet fitness", "la fitness", "adobe", "norton", "mcafee",
                "audible", "ancestry", "lifelock", "credit karma", "robinhood", "coinbase"]


def _detect_companies(text_low: str, external_url: str) -> list[str]:
    found: set[str] = set()
    for brand in KNOWN_BRANDS:
        if re.search(r"\b" + re.escape(brand) + r"\b", text_low):
            found.add(brand)
    if external_url:
        host = urlparse(external_url).netloc.lower().removeprefix("www.")
        token = host.split(".")[0] if host else ""
        # skip single/short tokens and reddit's own image/link hosts (redd.it, i.redd.it)
        if len(token) > 2 and "reddit" not in host and "redd" not in host:
            found.add(token)
    return sorted(found)


def enrich(post: dict) -> dict:
    text = f"{post.get('title', '')}\n\n{post.get('body', '')}".strip()
    low = text.lower()

    companies = _detect_companies(low, post.get("external_url", ""))

    complaint_type = "other"
    matched: list[str] = []
    for ctype, terms in COMPLAINT_TYPES.items():
        hits = [t for t in terms if t in low]
        if hits:
            matched.extend(hits)
            if complaint_type == "other":
                complaint_type = ctype

    legal_hits = [t for t in LEGAL_SIGNALS if t in low]
    matched.extend(legal_hits)
    money = 1 if MONEY_RE.search(text) else 0

    score = 0.0
    if legal_hits:
        score += 0.35
    if complaint_type != "other":
        score += 0.25
    if money:
        score += 0.15
    if companies:
        score += 0.10
    if len(legal_hits) >= 2:
        score += 0.10
    if len(matched) >= 4:
        score += 0.05
    signal_score = min(1.0, round(score, 3))

    return {
        "text": text,
        "companies": companies,
        "complaint_type": complaint_type,
        "keywords": sorted(set(matched)),
        "money_mentioned": money,
        "signal_score": signal_score,
    }
