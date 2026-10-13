import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, Iterable, Optional


DEFAULT_DATABASE_PATH = Path("data/mail_sanitizer.sqlite3")
PROMOTIONS_LABEL = "CATEGORY_PROMOTIONS"
CATEGORY_LABEL_IDS = {
    "promotions": "CATEGORY_PROMOTIONS",
    "primary": "CATEGORY_PERSONAL",
    "updates": "CATEGORY_UPDATES",
    "social": "CATEGORY_SOCIAL",
    "forums": "CATEGORY_FORUMS",
}
_INITIALIZED_DATABASES = set()
_INITIALIZATION_LOCK = Lock()
QUERY_LOGGER = logging.getLogger("gmail_sanitizer.query")


def _compact_sql(sql: str) -> str:
    return " ".join(sql.split())


def _execute(connection, sql: str, parameters=()):
    if sql.lstrip().upper().startswith("SELECT"):
        QUERY_LOGGER.info(
            "SQLite: %s | parameters=%r",
            _compact_sql(sql),
            tuple(parameters),
        )
    return connection.execute(sql, parameters)


def _initialize_schema(connection: sqlite3.Connection) -> None:
    _execute(
        connection,
        """
        CREATE TABLE IF NOT EXISTS messages (
            account_email TEXT NOT NULL,
            message_id TEXT NOT NULL,
            sender_email TEXT,
            headline TEXT,
            message_date TEXT,
            internal_date_ms INTEGER,
            label_ids TEXT NOT NULL,
            folder TEXT NOT NULL,
            is_unread INTEGER NOT NULL CHECK (is_unread IN (0, 1)),
            trashed INTEGER NOT NULL DEFAULT 0 CHECK (trashed IN (0, 1)),
            first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL,
            PRIMARY KEY (account_email, message_id)
        )
        """
    )
    columns = {
        row[1] for row in _execute(connection, "PRAGMA table_info(messages)").fetchall()
    }
    if "internal_date_ms" not in columns:
        _execute(connection, "ALTER TABLE messages ADD COLUMN internal_date_ms INTEGER")
    if "sender_email" not in columns:
        _execute(connection, "ALTER TABLE messages ADD COLUMN sender_email TEXT")
    if "trashed" not in columns:
        _execute(
            connection,
            "ALTER TABLE messages ADD COLUMN trashed INTEGER NOT NULL DEFAULT 0 "
            "CHECK (trashed IN (0, 1))"
        )


def _connect(database_path: Path) -> sqlite3.Connection:
    database_path = database_path.resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)

    if database_path not in _INITIALIZED_DATABASES:
        with _INITIALIZATION_LOCK:
            if database_path not in _INITIALIZED_DATABASES:
                try:
                    _initialize_schema(connection)
                    connection.commit()
                except Exception:
                    connection.close()
                    raise
                _INITIALIZED_DATABASES.add(database_path)

    return connection


def get_oldest_internal_date_ms(
    account_email: str,
    database_path: Path = DEFAULT_DATABASE_PATH,
    category: str = "all",
    unread_only: bool = False,
    include_archived: bool = True,
) -> Optional[int]:
    """Return the oldest stored Gmail date matching a database-dump scope."""
    query = """
        SELECT MIN(internal_date_ms)
        FROM messages
        WHERE account_email = ? AND internal_date_ms IS NOT NULL
    """
    parameters = [account_email]
    if unread_only:
        query += " AND is_unread = 1"
    if not include_archived:
        query += """
            AND EXISTS (
                SELECT 1 FROM json_each(messages.label_ids)
                WHERE json_each.value = 'INBOX'
            )
        """
    if category == "non-promotions":
        query += """
            AND NOT EXISTS (
                SELECT 1 FROM json_each(messages.label_ids)
                WHERE json_each.value = ?
            )
        """
        parameters.append(PROMOTIONS_LABEL)
    elif category != "all":
        query += """
            AND EXISTS (
                SELECT 1 FROM json_each(messages.label_ids)
                WHERE json_each.value = ?
            )
        """
        parameters.append(CATEGORY_LABEL_IDS[category])

    with _connect(Path(database_path)) as connection:
        row = _execute(connection, query, parameters).fetchone()
    return row[0] if row and row[0] is not None else None


def get_account_emails(
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> list[str]:
    """Return the Gmail accounts currently represented in the database."""
    with _connect(Path(database_path)) as connection:
        rows = _execute(
            connection,
            "SELECT DISTINCT account_email FROM messages ORDER BY account_email",
        ).fetchall()
    return [row[0] for row in rows]


def count_unread_promotions_by_sender(
    account_email: str,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> list[tuple[str, int]]:
    """Count locally stored unread, untrashed Promotions by sender."""
    with _connect(Path(database_path)) as connection:
        rows = _execute(
            connection,
            """
            SELECT sender_email, COUNT(*) AS message_count
            FROM messages
            WHERE account_email = ?
              AND is_unread = 1
              AND trashed = 0
              AND sender_email IS NOT NULL
              AND sender_email != ''
              AND EXISTS (
                  SELECT 1 FROM json_each(messages.label_ids)
                  WHERE json_each.value = 'CATEGORY_PROMOTIONS'
              )
            GROUP BY sender_email
            ORDER BY message_count ASC, sender_email COLLATE NOCASE
            """,
            (account_email,),
        ).fetchall()
    return [(sender_email, count) for sender_email, count in rows]


def get_cached_message_metadata(
    account_email: str,
    message_ids: Iterable[str],
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> Dict[str, Dict[str, Any]]:
    """Return Gmail-shaped metadata for message IDs already stored in SQLite."""
    ids = list(dict.fromkeys(message_ids))
    cached = {}
    if not ids:
        return cached

    with _connect(Path(database_path)) as connection:
        for start in range(0, len(ids), 500):
            chunk = ids[start:start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            rows = _execute(
                connection,
                f"""
                SELECT message_id, sender_email, headline, message_date,
                       internal_date_ms, label_ids
                FROM messages
                WHERE account_email = ?
                  AND message_id IN ({placeholders})
                """,
                (account_email, *chunk),
            ).fetchall()
            for message_id, sender, headline, message_date, internal_date, labels in rows:
                cached[message_id] = {
                    "id": message_id,
                    "internalDate": internal_date,
                    "labelIds": json.loads(labels),
                    "payload": {
                        "headers": [
                            {"name": "Subject", "value": headline or ""},
                            {"name": "From", "value": sender or ""},
                            {"name": "Date", "value": message_date or ""},
                        ]
                    },
                }
    return cached


def list_promotions_by_sender(
    account_email: str,
    sender_email: str,
    unread_only: bool,
    max_results: int,
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> list[Dict[str, Any]]:
    """Return untrashed Promotions messages for a sender from SQLite."""
    max_results = max(1, max_results)
    query = """
        SELECT message_id, sender_email, headline, message_date,
               internal_date_ms, label_ids, folder, is_unread
        FROM messages
        WHERE account_email = ?
          AND sender_email = ? COLLATE NOCASE
          AND trashed = 0
          AND EXISTS (
              SELECT 1 FROM json_each(messages.label_ids)
              WHERE json_each.value = 'CATEGORY_PROMOTIONS'
          )
    """
    parameters = [account_email, sender_email.strip()]
    if unread_only:
        query += " AND is_unread = 1"
    query += " ORDER BY internal_date_ms DESC LIMIT ?"
    parameters.append(max_results)

    with _connect(Path(database_path)) as connection:
        rows = _execute(connection, query, parameters).fetchall()

    return [
        {
            "id": message_id,
            "from": sender,
            "subject": headline,
            "date": message_date,
            "internalDate": internal_date,
            "labelIds": json.loads(labels),
            "folder": folder,
            "isUnread": bool(is_unread),
        }
        for (
            message_id,
            sender,
            headline,
            message_date,
            internal_date,
            labels,
            folder,
            is_unread,
        ) in rows
    ]


def upsert_messages(
    account_email: str,
    messages: Iterable[Dict[str, Any]],
    database_path: Path = DEFAULT_DATABASE_PATH,
) -> int:
    """Store the latest state of Gmail messages regardless of category."""
    timestamp = datetime.now(timezone.utc).isoformat()
    rows = []

    for message in messages:
        labels = list(message.get("labelIds") or [])
        rows.append(
            (
                account_email,
                message["id"],
                message.get("from"),
                message.get("subject"),
                message.get("date"),
                message.get("internalDate"),
                json.dumps(labels),
                message.get("folder") or "(no labels)",
                int("UNREAD" in labels),
                int("TRASH" in labels),
                timestamp,
                timestamp,
            )
        )

    if not rows:
        return 0

    with _connect(Path(database_path)) as connection:
        connection.executemany(
            """
            INSERT INTO messages (
                account_email, message_id, sender_email, headline, message_date,
                internal_date_ms, label_ids, folder, is_unread,
                trashed, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_email, message_id) DO UPDATE SET
                sender_email = excluded.sender_email,
                headline = excluded.headline,
                message_date = excluded.message_date,
                internal_date_ms = excluded.internal_date_ms,
                label_ids = excluded.label_ids,
                folder = excluded.folder,
                is_unread = excluded.is_unread,
                trashed = excluded.trashed,
                last_seen_at = excluded.last_seen_at
            """,
            rows,
        )

    return len(rows)


def mark_message_trashed(
    account_email: str,
    message_id: str,
    database_path: Path = DEFAULT_DATABASE_PATH,
    label_ids: Optional[Iterable[str]] = None,
) -> bool:
    """Record a successful Gmail Trash operation and commit it immediately."""
    timestamp = datetime.now(timezone.utc).isoformat()
    with _connect(Path(database_path)) as connection:
        row = _execute(
            connection,
            """
            SELECT label_ids
            FROM messages
            WHERE account_email = ? AND message_id = ?
            """,
            (account_email, message_id),
        ).fetchone()
        if row is None:
            return False

        if label_ids is None:
            labels = [label for label in json.loads(row[0]) if label != "INBOX"]
            if "TRASH" not in labels:
                labels.append("TRASH")
        else:
            labels = list(label_ids)

        _execute(
            connection,
            """
            UPDATE messages
            SET label_ids = ?, folder = 'TRASH', is_unread = ?,
                trashed = 1, last_seen_at = ?
            WHERE account_email = ? AND message_id = ?
            """,
            (
                json.dumps(labels),
                int("UNREAD" in labels),
                timestamp,
                account_email,
                message_id,
            ),
        )
    return True
