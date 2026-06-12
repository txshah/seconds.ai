import os
import html
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def _load_firm_registry() -> list[dict]:
    firms = []
    i = 1
    while True:
        name = os.environ.get(f"FIRM_{i}_NAME")
        if not name:
            break

        firms.append({
            "name": name,
            "chat_id": os.environ.get(f"FIRM_{i}_CHAT_ID", ""),
            "label_keywords": [
                kw.strip()
                for kw in os.environ.get(f"FIRM_{i}_KEYWORDS", "*").split(",")
                if kw.strip()
            ],
        })
        i += 1

    return firms


def _firm_matches(firm: dict, pioneer_label: str, complaint_type: str) -> bool:
    keywords = firm.get("label_keywords") or ["*"]
    return any(
        kw == "*"
        or kw.lower() in (pioneer_label or "").lower()
        or kw.lower() in (complaint_type or "").lower()
        for kw in keywords
    )


def route_leads(pioneer_data: dict) -> list[dict]:
    label = pioneer_data.get("pioneer_label") or ""
    complaint_type = pioneer_data.get("complaint_type") or ""
    firms = _load_firm_registry()
    return [firm for firm in firms if _firm_matches(firm, label, complaint_type)]


def _score_to_percent(value) -> int:
    try:
        value = float(value or 0)
    except Exception:
        return 0

    if value <= 1:
        value = value * 100

    return round(value)


def _keywords_to_text(value) -> str:
    if isinstance(value, list):
        return ", ".join(str(x) for x in value)
    if isinstance(value, str):
        return value
    return "N/A"


def send_lead_to_firm(pioneer_data: dict, chat_id: str) -> dict:
    token = os.environ.get("TELEGRAM_BOT_ID")
    if not token:
        return {
            "ok": False,
            "error": "TELEGRAM_BOT_ID is not configured",
        }

    post_text = pioneer_data.get("post") or pioneer_data.get("text") or "N/A"
    if len(post_text) > 1200:
        post_text = post_text[:1200] + "..."

    message = (
        f"<b>New Lead Detected by seconds.ai</b>\n\n"
        f"<b>Classification:</b> {html.escape(str(pioneer_data.get('pioneer_label') or 'N/A')).title()}\n"
        f"<b>Complaint Type:</b> {html.escape(str(pioneer_data.get('complaint_type') or 'N/A'))}\n"
        f"<b>Pioneer Score:</b> {_score_to_percent(pioneer_data.get('pioneer_score') or pioneer_data.get('risk_score'))}%\n"
        f"<b>Signal Score:</b> {_score_to_percent(pioneer_data.get('signal_score'))}%\n\n"
        f"<b>Evidence:</b>\n{html.escape(str(post_text))}\n\n"
        f"<b>Source:</b> {html.escape(str(pioneer_data.get('source_url') or 'N/A'))}\n"
        f"<b>Keywords:</b> {html.escape(_keywords_to_text(pioneer_data.get('keywords')))}\n"
        f"<b>Post ID:</b> <code>{html.escape(str(pioneer_data.get('post_id') or 'N/A'))}</code>\n\n"
        f"<i>Demo alert only — review source evidence before any action.</i>"
    )

    response = requests.post(
        TELEGRAM_API.format(token=token),
        json={
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        },
        timeout=20,
    )

    try:
        data = response.json()
    except Exception:
        data = {"ok": False, "raw_response": response.text}

    data["http_status"] = response.status_code
    return data
