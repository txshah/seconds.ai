import os
import requests
import clickhouse_connect
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"

# Registry of subscribed law firms.
# label_keywords matches against pioneer_label or complaint_type.
# Use "*" as a wildcard that matches any lead.
FIRM_REGISTRY = [
    {
        "name": "McCarthy Law",
        "chat_id": os.environ.get("TELEGRAM_MCCARTHY_LAW", ""),
        "label_keywords": ["fdcpa", "debt_collection"],
    },
    {
        "name": "Rivera & Partners",
        "chat_id": os.environ.get("TELEGRAM_RIVERA_PARTNERS", ""),
        "label_keywords": ["fdcpa", "fcra", "debt_collection"],
    },
    {
        "name": "Sam & Ash Law",
        "chat_id": os.environ.get("TELEGRAM_SAM_ASH_LAW", ""),
        "label_keywords": ["product liability", "defective_product"],
    },
    {
        "name": "General Consumer Rights Group",
        "chat_id": os.environ.get("TELEGRAM_GENERAL_CONSUMER", ""),
        "label_keywords": ["*"],
    },
]


def fetch_leads_from_clickhouse(min_score: float = 0.9) -> list[dict]:
    """Pull high-confidence leads from ClickHouse."""
    client = clickhouse_connect.get_client(
        host=os.environ["CLICKHOUSE_HOST"],
        port=int(os.environ["CLICKHOUSE_PORT"]),
        username=os.environ["CLICKHOUSE_USER"],
        password=os.environ["CLICKHOUSE_PASSWORD"],
        database=os.environ["CLICKHOUSE_DATABASE"],
        secure=True,
    )
    result = client.query(
        "SELECT post_id, post, pioneer_score, pioneer_label, "
        "complaint_type, source_url, signal_score, keywords "
        "FROM posts WHERE signal_score >= {score:Float32} "
        "ORDER BY signal_score DESC",
        parameters={"score": min_score},
    )
    columns = result.column_names
    return [dict(zip(columns, row)) for row in result.result_rows]


def _firm_matches(firm: dict, pioneer_label: str, complaint_type: str) -> bool:
    return any(
        kw == "*"
        or kw.lower() in pioneer_label.lower()
        or kw.lower() in complaint_type.lower()
        for kw in firm["label_keywords"]
    )


def route_leads(pioneer_data: dict) -> list[dict]:
    """Return all firms from FIRM_REGISTRY that match the pioneer_data context."""
    label = pioneer_data.get("pioneer_label") or ""
    complaint_type = pioneer_data.get("complaint_type") or ""
    return [f for f in FIRM_REGISTRY if _firm_matches(f, label, complaint_type)]


def send_lead_to_firm(pioneer_data: dict, chat_id: str) -> dict:
    """
    Takes a pioneer.ai scored output dict and sends a lead alert
    to a law firm's Telegram chat via Composio.

    Expected keys in pioneer_data:
        post_id, post, pioneer_score, pioneer_label,
        complaint_type, source_url, keywords, signal_score
    """
    message = (
        f"<b>New Lead Detected by seconds.ai</b>\n\n"
        f"<b>Classification:</b> {(pioneer_data.get('pioneer_label') or 'N/A').title()}\n"
        f"<b>Complaint Type:</b> {pioneer_data.get('complaint_type', 'N/A')}\n"
        f"<b>Pioneer Score:</b> {round((pioneer_data.get('pioneer_score') or 0) * 100)}%\n"
        f"<b>Signal Score:</b> {round((pioneer_data.get('signal_score') or 0) * 100)}%\n\n"
        f"<b>Complaint:</b>\n{pioneer_data.get('post', 'N/A')}\n\n"
        f"<b>Source:</b> {pioneer_data.get('source_url', 'N/A')}\n"
        f"<b>Keywords:</b> {', '.join(pioneer_data.get('keywords') or [])}\n"
        f"<b>Post ID:</b> <code>{pioneer_data.get('post_id', 'N/A')}</code>\n\n"
        f"<i>Powered by seconds.ai — review and reach out to the consumer directly.</i>"
    )

    token = os.environ["TELEGRAM_BOT_ID"]
    r = requests.post(
        TELEGRAM_API.format(token=token),
        json={"chat_id": chat_id, "text": message, "parse_mode": "HTML"},
    )
    return r.json()


def process_pioneer_batch(leads: list[dict]) -> None:
    """Route and send Telegram messages for a list of Pioneer-scored leads."""
    for lead in leads:
        matched_firms = route_leads(lead)
        if not matched_firms:
            print(f"[{lead.get('post_id')}] No matching firms — skipping.")
            continue
        for firm in matched_firms:
            if not firm["chat_id"]:
                print(f"[{lead.get('post_id')}] Skipping {firm['name']} — chat_id not set in .env")
                continue
            print(f"[{lead.get('post_id')}] Sending to {firm['name']} → {firm['chat_id']}")
            result = send_lead_to_firm(lead, firm["chat_id"])
            print(result)


if __name__ == "__main__":
    print("Fetching leads from ClickHouse...")
    leads = fetch_leads_from_clickhouse(min_score=0.9)
    print(f"Found {len(leads)} leads.")
    process_pioneer_batch(leads)
