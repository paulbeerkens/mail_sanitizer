import logging
import re
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Callable, Optional


GMAIL_PAGE_SIZE = 50
DEFAULT_PAGE_DELAY_SECONDS = 1
DEFAULT_TRASH_DELAY_SECONDS = 0.1
DATABASE_DUMP_CATEGORIES = (
    "promotions",
    "primary",
    "updates",
    "social",
    "forums",
    "non-promotions",
    "all",
)
QUERY_LOGGER = logging.getLogger("gmail_sanitizer.query")


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


def _build_headline_entry(msg: Dict[str, Any]) -> Dict[str, Any]:
    headers = msg.get("payload", {}).get("headers", [])
    subject = _get_header(headers, "Subject")
    sender = _get_header(headers, "From")
    email = _extract_email(sender)
    date = _get_header(headers, "Date")
    snippet = msg.get("snippet", "")
    labels = msg.get("labelIds", [])
    internal_date = msg.get("internalDate")
    folder = "INBOX" if "INBOX" in labels else ", ".join(labels) if labels else "(no labels)"
    return {
        "id": msg["id"],
        "subject": subject,
        "from": email,
        "date": date,
        "internalDate": int(internal_date) if internal_date is not None else None,
        "snippet": snippet,
        "folder": folder,
        "labelIds": labels,
        "isUnread": "UNREAD" in labels,
    }


def _build_query(
    sender_email: Optional[str],
    unread_only: bool = True,
    promotions_only: bool = True,
    before_epoch_seconds: Optional[int] = None,
    in_anywhere: bool = False,
    category: Optional[str] = None,
    inbox_only: bool = False,
) -> str:
    query_parts = []
    if inbox_only:
        query_parts.append("in:inbox")
    if unread_only:
        query_parts.append("is:unread")
    if sender_email:
        query_parts.append(f"from:{sender_email.strip()}")
    if promotions_only:
        query_parts.append("label:promotions")
    elif category and category != "all":
        if category == "non-promotions":
            query_parts.append("-category:promotions")
        else:
            query_parts.append(f"category:{category}")
    if before_epoch_seconds is not None:
        query_parts.append(f"before:{before_epoch_seconds}")
    if in_anywhere:
        query_parts.append("in:anywhere")
    query = " ".join(query_parts)
    QUERY_LOGGER.info("Gmail: %r", query)
    return query


def _get_message_metadata(service, message_id: str) -> Dict[str, Any]:
    return service.users().messages().get(
        userId="me",
        id=message_id,
        format="metadata",
        metadataHeaders=["Subject", "From", "Date"],
    ).execute()


def _format_internal_date(internal_date: Optional[str]) -> str:
    if internal_date is None:
        return "(unknown)"
    timestamp = int(internal_date) / 1000
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone().isoformat(timespec="seconds")


def _print_page_progress(page_count: int, total_count: int, oldest_message: Dict[str, Any]) -> None:
    current_time = datetime.now().astimezone().isoformat(timespec="seconds")
    oldest_date = _format_internal_date(oldest_message.get("internalDate"))
    print(
        f"[{current_time}] page messages: {page_count}; "
        f"oldest message: {oldest_date}; total received: {total_count}"
    )


def _list_message_references(
    service,
    query: str,
    max_results: int,
    page_delay_seconds: float = DEFAULT_PAGE_DELAY_SECONDS,
    metadata_cache: Optional[Dict[str, Dict[str, Any]]] = None,
    page_callback: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    metadata_provider: Optional[
        Callable[[List[str]], Dict[str, Dict[str, Any]]]
    ] = None,
) -> List[Dict[str, str]]:
    """Return up to max_results message references, following Gmail page tokens."""
    max_results = max(1, max_results)
    page_delay_seconds = max(0.0, page_delay_seconds)
    messages = []
    page_token = None
    if metadata_cache is None:
        metadata_cache = {}

    while len(messages) < max_results:
        request_args = {
            "userId": "me",
            "q": query,
            "maxResults": min(GMAIL_PAGE_SIZE, max_results - len(messages)),
        }
        if page_token:
            request_args["pageToken"] = page_token

        results = service.users().messages().list(**request_args).execute()
        page_messages = results.get("messages", [])
        messages.extend(page_messages)
        if page_messages:
            if metadata_provider:
                page_ids = [message["id"] for message in page_messages]
                metadata_cache.update(metadata_provider(page_ids))
            oldest_id = page_messages[-1]["id"]
            oldest_message = metadata_cache.get(oldest_id)
            if oldest_message is None:
                oldest_message = _get_message_metadata(service, oldest_id)
                metadata_cache[oldest_id] = oldest_message
            _print_page_progress(len(page_messages), len(messages), oldest_message)

            if page_callback:
                page_entries = []
                for message in page_messages:
                    metadata = metadata_cache.get(message["id"]) or _get_message_metadata(
                        service, message["id"]
                    )
                    metadata_cache[message["id"]] = metadata
                    page_entries.append(_build_headline_entry(metadata))
                page_callback(page_entries)
        else:
            _print_page_progress(0, len(messages), {})
        page_token = results.get("nextPageToken")
        if not page_token:
            break
        if len(messages) < max_results and page_delay_seconds:
            time.sleep(page_delay_seconds)

    return messages[:max_results]


def list_unread_headlines(
    service,
    max_results: int = 1,
    page_delay_seconds: float = DEFAULT_PAGE_DELAY_SECONDS,
    metadata_provider: Optional[
        Callable[[List[str]], Dict[str, Dict[str, Any]]]
    ] = None,
) -> List[Dict[str, Any]]:
    """Return up to `max_results` unread messages with subject and snippet.

    Each item is a dict: {"id", "subject", "from", "date", "snippet"}
    """
    return list_unread_headlines_by_sender(
        service,
        None,
        max_results=max_results,
        page_delay_seconds=page_delay_seconds,
        metadata_provider=metadata_provider,
    )


def list_unread_headlines_by_sender(
    service,
    sender_email: Optional[str],
    max_results: int = 50,
    page_delay_seconds: float = DEFAULT_PAGE_DELAY_SECONDS,
    metadata_provider: Optional[
        Callable[[List[str]], Dict[str, Dict[str, Any]]]
    ] = None,
) -> List[Dict[str, Any]]:
    """Return unread messages, optionally filtered to a sender email address."""
    return list_headlines_by_sender(
        service,
        sender_email,
        unread_only=True,
        max_results=max_results,
        page_delay_seconds=page_delay_seconds,
        metadata_provider=metadata_provider,
    )


def list_headlines_by_sender(
    service,
    sender_email: Optional[str],
    unread_only: bool,
    max_results: int = 50,
    page_delay_seconds: float = DEFAULT_PAGE_DELAY_SECONDS,
    metadata_provider: Optional[
        Callable[[List[str]], Dict[str, Dict[str, Any]]]
    ] = None,
) -> List[Dict[str, Any]]:
    """Return Promotions messages from a sender, optionally restricted to unread."""
    query = _build_query(sender_email, unread_only=unread_only, promotions_only=True)
    metadata_cache = {}
    messages = _list_message_references(
        service,
        query,
        max_results,
        page_delay_seconds,
        metadata_cache,
        metadata_provider=metadata_provider,
    )
    out = []
    for m in messages:
        msg = metadata_cache.get(m["id"]) or _get_message_metadata(service, m["id"])
        out.append(_build_headline_entry(msg))
    return out


def archive_messages(
    service,
    message_ids: List[str],
    trash_delay_seconds: float = DEFAULT_TRASH_DELAY_SECONDS,
    progress_callback: Optional[
        Callable[[str, Dict[str, Any], int, int], None]
    ] = None,
) -> int:
    """Move messages to Trash and return the number changed."""
    trash_delay_seconds = max(0.0, trash_delay_seconds)
    archived_count = 0
    total_count = len(message_ids)
    for index, message_id in enumerate(message_ids, start=1):
        trashed_message = service.users().messages().trash(
            userId="me",
            id=message_id,
        ).execute()
        archived_count += 1
        if progress_callback:
            progress_callback(message_id, trashed_message, index, total_count)
        if index < total_count and trash_delay_seconds:
            time.sleep(trash_delay_seconds)

    return archived_count


def get_message_ids_for_sender(
    service,
    sender_email: Optional[str],
    max_results: int = 50,
    page_delay_seconds: float = DEFAULT_PAGE_DELAY_SECONDS,
) -> List[str]:
    """Return the message IDs for unread messages filtered by sender email."""
    query = _build_query(sender_email, unread_only=True)
    messages = _list_message_references(service, query, max_results, page_delay_seconds)
    return [m["id"] for m in messages]


def list_messages_by_sender(
    service,
    sender_email: Optional[str],
    max_results: int = 50,
    page_delay_seconds: float = DEFAULT_PAGE_DELAY_SECONDS,
    metadata_provider: Optional[
        Callable[[List[str]], Dict[str, Dict[str, Any]]]
    ] = None,
    in_anywhere: bool = False,
) -> List[Dict[str, Any]]:
    """Return messages by sender without requiring them to be unread."""
    query = _build_query(
        sender_email,
        unread_only=False,
        promotions_only=False,
        in_anywhere=in_anywhere,
    )
    metadata_cache = {}
    messages = _list_message_references(
        service,
        query,
        max_results,
        page_delay_seconds,
        metadata_cache,
        metadata_provider=metadata_provider,
    )
    out = []
    for m in messages:
        msg = metadata_cache.get(m["id"]) or _get_message_metadata(service, m["id"])
        out.append(_build_headline_entry(msg))
    return out


def list_messages_for_database_dump(
    service,
    max_results: int = 50,
    page_delay_seconds: float = DEFAULT_PAGE_DELAY_SECONDS,
    before_epoch_seconds: Optional[int] = None,
    page_callback: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
    category: str = "promotions",
    unread_only: bool = False,
    include_archived: bool = False,
) -> List[Dict[str, Any]]:
    """Return messages matching the requested database-dump scope."""
    if category not in DATABASE_DUMP_CATEGORIES:
        raise ValueError(f"Unsupported Gmail category: {category}")
    query = _build_query(
        None,
        unread_only=unread_only,
        promotions_only=False,
        before_epoch_seconds=before_epoch_seconds,
        category=category,
        inbox_only=not include_archived,
    )
    metadata_cache = {}
    messages = _list_message_references(
        service,
        query,
        max_results,
        page_delay_seconds,
        metadata_cache,
        page_callback,
    )
    out = []
    for message in messages:
        msg = metadata_cache.get(message["id"]) or _get_message_metadata(
            service, message["id"]
        )
        out.append(_build_headline_entry(msg))
    return out
