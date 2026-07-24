import re
from typing import List, Dict, Any, Optional


def _get_header(headers: List[Dict[str, str]], name: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _extract_email(value: str) -> str:
    if not value:
        return ""

    match = re.search(r"<([^>]+)>", value)
    if match:
        return match.group(1)

    return value


def _build_headline_entry(msg: Dict[str, Any]) -> Dict[str, str]:
    headers = msg.get("payload", {}).get("headers", [])
    subject = _get_header(headers, "Subject")
    sender = _get_header(headers, "From")
    email = _extract_email(sender)
    date = _get_header(headers, "Date")
    snippet = msg.get("snippet", "")
    labels = msg.get("labelIds", [])
    filtered_labels = [label for label in labels if label != "INBOX"]
    folder = "INBOX" if "INBOX" in labels else ", ".join(labels) if labels else "(no labels)"
    return {
        "id": msg["id"],
        "subject": subject,
        "from": email,
        "date": date,
        "snippet": snippet,
        "folder": folder,
        "labelIds": filtered_labels,
    }


def _build_query(sender_email: Optional[str], include_unread: bool = True, include_promotions_label: bool = True) -> str:
    query = "is:unread" if include_unread else ""
    if sender_email:
        query = f"{query} from:{sender_email.strip()}" if query else f"from:{sender_email.strip()}"
    if include_promotions_label:
        query = f"{query} label:promotions" if query else "label:promotions"
    return query


def list_unread_headlines(service, max_results: int = 1) -> List[Dict[str, str]]:
    """Return up to `max_results` unread messages with subject and snippet.

    Each item is a dict: {"id", "subject", "from", "date", "snippet"}
    """
    return list_unread_headlines_by_sender(service, None, max_results=max_results)


def list_unread_headlines_by_sender(service, sender_email: Optional[str], max_results: int = 50) -> List[Dict[str, str]]:
    """Return unread messages, optionally filtered to a sender email address."""
    max_results = max(1, max_results)
    query = _build_query(sender_email, include_unread=True)

    results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = results.get("messages", [])[:max_results]
    out = []
    for m in messages:
        msg = service.users().messages().get(
            userId="me",
            id=m["id"],
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()
        out.append(_build_headline_entry(msg))
    return out


def archive_messages_by_sender(service, sender_email: Optional[str], max_results: int = 50) -> int:
    """Archive unread messages from a sender email address and return the number archived."""
    max_results = max(1, max_results)
    query = _build_query(sender_email)

    results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = results.get("messages", [])[:max_results]

    archived_count = 0
    for m in messages:
        service.users().messages().modify(
            userId="me",
            id=m["id"],
            body={"removeLabelIds": ["INBOX"], "addLabelIds": ["TRASH"]},
        ).execute()
        archived_count += 1

    return archived_count


def get_message_ids_for_sender(service, sender_email: Optional[str], max_results: int = 50) -> List[str]:
    """Return the message IDs for unread messages filtered by sender email."""
    max_results = max(1, max_results)
    query = _build_query(sender_email, include_unread=True)

    results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = results.get("messages", [])[:max_results]
    return [m["id"] for m in messages]


def list_messages_by_sender(service, sender_email: Optional[str], max_results: int = 50) -> List[Dict[str, str]]:
    """Return messages by sender without requiring them to be unread."""
    max_results = max(1, max_results)
    query = _build_query(sender_email, include_unread=False, include_promotions_label=False)

    results = service.users().messages().list(userId="me", q=query, maxResults=max_results).execute()
    messages = results.get("messages", [])[:max_results]
    out = []
    for m in messages:
        msg = service.users().messages().get(
            userId="me",
            id=m["id"],
            format="metadata",
            metadataHeaders=["Subject", "From", "Date"],
        ).execute()
        out.append(_build_headline_entry(msg))
    return out
