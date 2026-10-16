# gmail-sanitizer

Minimal Python starter app to interact with Gmail via the Gmail API. This first version lists the 10 most recent unread message headlines.

Setup

1. Create a Google Cloud project and enable the Gmail API.
2. Create OAuth 2.0 Client ID (Desktop), download the JSON and save it as `credentials.json` in the project root.
3. Install dependencies:

```bash
python3.14 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

Run

```bash
.venv/bin/python main.py
```

Use `--max-results` to control the total number of messages retrieved. The app
automatically follows Gmail page tokens when the requested total spans multiple
API pages.

Gmail search queries and SQLite `SELECT` statements are logged by default.
Suppress them with `--no-query-logging`:

```bash
.venv/bin/python main.py --no-query-logging
```

Count unread Promotions messages by sender using SQLite only:

```bash
.venv/bin/python main.py --count
```

This command does not authenticate with Google or call the Gmail API. If the
database contains more than one Gmail account, select one with
`--account-email user@example.com`.

Metadata storage

Retrieved messages are stored in `data/mail_sanitizer.sqlite3` regardless of
their Gmail category. The database keeps one row per Gmail account and message
ID and updates that row with the latest known headline, date, complete label
list, sender email, folder, and unread state. `account_email` identifies the
mailbox being cataloged, while `sender_email` comes from the message's `From`
header.

Populate or refresh the database with Promotions messages currently in the
Inbox:

```bash
.venv/bin/python main.py --database-dump --max-results 2000 --page-delay 0.25
```

`promotions` is the default category. Select another Gmail category with
`--category`:

```bash
.venv/bin/python main.py --database-dump --category primary --unread-only \
  --max-results 2000
```

Supported category values are `promotions`, `primary`, `updates`, `social`,
`forums`, `non-promotions`, and `all`. The example above generates the Gmail
query `in:inbox is:unread category:primary`. To retrieve every unread Inbox
message except Promotions, use:

```bash
.venv/bin/python main.py --database-dump --category non-promotions \
  --unread-only --max-results 2000
```

Database dumps search the Inbox by default. Add `--include-archived` to include
messages outside the Inbox.

To store read and unread messages from one sender across all categories and
locations except Spam and Trash, use:

```bash
.venv/bin/python main.py --database-dump \
  --sender-email sender@example.com --max-results 10000
```

`--sender-email` automatically uses all categories and includes archived mail,
so the Gmail query is `from:sender@example.com`. Gmail's normal search behavior
excludes Spam and Trash. The retrieved metadata is written to SQLite after each
completed page.

The sender-specific dump can be extended backward using a history boundary
calculated only from that sender's stored messages:

```bash
.venv/bin/python main.py --database-dump \
  --sender-email sender@example.com --expand-history --max-results 10000
```

Gmail uses `primary` in search queries but returns `CATEGORY_PERSONAL` in the
API message `labelIds`; the application maps between those names when querying
the local database.

The number is the maximum total to retrieve, not the size of a single page.
`--page-delay` controls the pause between result pages; it defaults to 1
seconds and can be set to `0` to disable the throttle.

Database dumps commit after each Gmail result page. If a later request is
interrupted or fails, rows from every completed page remain stored.

To extend a category dump with messages older than its current oldest matching
record, repeat the same filters and add `--expand-history`:

```bash
.venv/bin/python main.py --database-dump --category primary --unread-only \
  --expand-history --max-results 2000
```

The command reads the oldest Gmail internal timestamp in SQLite that matches
the selected category, unread status, and Inbox/archive scope, then adds a
`before:` constraint to the Gmail search. A one-second overlap prevents
messages at the boundary from being missed; existing rows are safely updated by
the database upsert. Run a normal `--database-dump` with the same filters first
to establish the initial history boundary.

Trash messages from one sender

```bash
.venv/bin/python main.py --archive-email sender@example.com \
  --max-results 500 --page-delay 0.25 --trash-delay 0.1
```

The initial preview follows Gmail result pages and is shown before a single
confirmation prompt. After confirmation, messages are moved to Trash one at a
time. By default, the app pauses for 0.1 seconds after every successful
operation. `--trash-delay` controls this pause. Progress is printed after every
successful operation. The app uses Gmail's explicit `messages.trash` method.
Each success is committed immediately to the database by setting `trashed` to
`1` and storing the `labelIds` returned by Gmail.

To include both read and unread Promotions messages from the sender, use:

```bash
.venv/bin/python main.py --archive-all-promotional-email sender@example.com --max-results 500
```

`--archive-email` and `--archive-all-promotional-email` are mutually exclusive. Both use
the SQLite database for their initial message selection and use the same
preview, confirmation, Trash throttling, progress, and per-message database
commits. `--archive-email` selects only unread, untrashed records;
`--archive-all-promotional-email` selects and previews every read and unread
untrashed Promotions record. The actual Trash operation is limited to the
newest `--max-results` messages. After it finishes, the app runs the equivalent
of `--all-email` without `--in-anywhere` and displays any messages from that
sender that remain outside Spam and Trash.

To trash every locally stored Inbox message from a sender across all Gmail
categories, use:

```bash
.venv/bin/python main.py --archive-all-inbox-email sender@example.com
```

This command loads every matching untrashed Inbox record from SQLite without a
preview limit and prints all headlines before making changes. The actual Trash
operation is limited to the newest `--max-results` messages. It then requires
an explicit yes/no confirmation. Only messages carrying the `INBOX` label are
selected; archived, Spam, and Trash messages are excluded.

After the Trash operations finish, the command queries Gmail with
`from:sender@example.com in:inbox` and displays up to `--max-results` messages
from that sender that still remain in the Inbox.

To process the top unread Inbox senders while excluding every address in the
`archive_list` table, use:

```bash
.venv/bin/python main.py --archive-top-inbox-senders 3 \
  --refresh-max-results 100000 --max-results 3500 --page-delay 5
```

The ranking excludes Promotions and Social messages, trashed rows, and sender
addresses found in `archive_list` (case-insensitively). The candidate list is
captured and displayed clearly before processing starts. A ten-second
countdown lets the user type `q` and press Enter to cancel the entire run before
Gmail is contacted. For each candidate, the app refreshes that sender from
Gmail, previews every current Inbox headline, requests a separate confirmation,
trashes at most `--max-results` newest messages, and checks Gmail for what
remains in the Inbox. Cancelling one sender continues with the next.

All archive commands protect messages whose headlines contain `purchase`,
`purchased`, or `invoice`, using case-insensitive substring matching. Protected
messages are omitted from archive previews and Trash operations. The top-sender
ranking also excludes them from its counts.

For previews and sender queries, Gmail supplies the current matching message
IDs and metadata is loaded from SQLite when available. The app calls Gmail
`messages.get` only for IDs missing from the database. Retrieved metadata is
stored regardless of category. Database dumps bypass this cache intentionally
so they refresh the latest Gmail state.

By default, `--all-email` follows Gmail's normal search behavior and excludes
Spam and Trash. Include those locations with:

```bash
.venv/bin/python main.py --all-email sender@example.com --in-anywhere
```

Files

- `main.py` — example runner
- `gmail_sanitizer/auth.py` — OAuth helper
- `gmail_sanitizer/client.py` — Gmail client helpers
- `gmail_sanitizer/database.py` — SQLite latest-state storage
# mail_sanitizer
