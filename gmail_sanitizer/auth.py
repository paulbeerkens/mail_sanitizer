import os
import pickle
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

# If modifying these scopes, delete the file token.json.
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


def _has_required_scopes(creds: Optional[Credentials]) -> bool:
    if not creds:
        return False

    granted_scopes = set(get_granted_scopes(creds))
    required_scopes = set(SCOPES)
    return required_scopes.issubset(granted_scopes)


def get_credentials(credentials_path: str = "credentials.json", token_path: str = "token.json") -> Optional[Credentials]:
    creds = None
    if os.path.exists(token_path):
        creds = Credentials.from_authorized_user_file(token_path, SCOPES)
        if creds and creds.valid and not _has_required_scopes(creds):
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid or not _has_required_scopes(creds):
            if not os.path.exists(credentials_path):
                raise FileNotFoundError(f"Missing {credentials_path}. Create OAuth credentials in Google Cloud Console.")
            flow = InstalledAppFlow.from_client_secrets_file(credentials_path, SCOPES)
            creds = flow.run_local_server(port=0)
        # Save the credentials for the next run
        with open(token_path, "w") as token:
            token.write(creds.to_json())

    return creds


def get_granted_scopes(creds: Optional[Credentials]) -> list[str]:
    return list(getattr(creds, "scopes", []) or [])
