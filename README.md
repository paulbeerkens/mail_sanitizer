# gmail-sanitizer

Minimal Python starter app to interact with Gmail via the Gmail API. This first version lists the 10 most recent unread message headlines.

Setup

1. Create a Google Cloud project and enable the Gmail API.
2. Create OAuth 2.0 Client ID (Desktop), download the JSON and save it as `credentials.json` in the project root.
3. Install dependencies:

```bash
python3 -m pip install -r requirements.txt
```

Run

```bash
python3 main.py
```

Files

- `main.py` — example runner
- `gmail_sanitizer/auth.py` — OAuth helper
- `gmail_sanitizer/client.py` — Gmail client helpers
# mail_sanitizer
