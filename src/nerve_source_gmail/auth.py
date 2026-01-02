"""Authentication for Gmail API access.

Uses local file-based credentials (same as gmail-cli) or service account
with domain-wide delegation.
"""

import logging
import pickle
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google.oauth2 import service_account

logger = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]

# Config paths (same as gmail-cli)
CONFIG_DIR = Path.home() / ".gmail-cli"
ACCOUNTS_DIR = CONFIG_DIR / "accounts"
SERVICE_ACCOUNT_FILE = CONFIG_DIR / "service_account.json"

# Alternative locations
CLAUDE_SECRETS_DIR = Path.home() / ".claude" / "secrets"
CLAUDE_SERVICE_ACCOUNT_FILE = CLAUDE_SECRETS_DIR / "gmail_service_account.json"


def get_account_token_path(account: str) -> Path:
    """Get the token file path for a specific account."""
    safe_name = account.replace("@", "_at_").replace(".", "_")
    account_dir = ACCOUNTS_DIR / safe_name
    return account_dir / "token.pickle"


def find_service_account_file() -> Path | None:
    """Find service account file in standard locations."""
    if SERVICE_ACCOUNT_FILE.exists():
        return SERVICE_ACCOUNT_FILE
    if CLAUDE_SERVICE_ACCOUNT_FILE.exists():
        return CLAUDE_SERVICE_ACCOUNT_FILE
    return None


def load_credentials_json() -> dict | None:
    """Load OAuth client credentials from credentials.json."""
    creds_file = CONFIG_DIR / "credentials.json"
    if not creds_file.exists():
        creds_file = CLAUDE_SECRETS_DIR / "gmail_client_secret.json"

    if not creds_file.exists():
        return None

    import json
    with open(creds_file) as f:
        data = json.load(f)

    # Handle both "installed" and "web" credential types
    if "installed" in data:
        return data["installed"]
    elif "web" in data:
        return data["web"]
    return data


def load_user_credentials(user_id: str) -> Optional[Credentials]:
    """Load saved OAuth credentials for a user.

    Args:
        user_id: User email address

    Returns:
        Credentials if found, None otherwise
    """
    token_path = get_account_token_path(user_id)
    if not token_path.exists():
        return None

    try:
        with open(token_path, "rb") as f:
            creds = pickle.load(f)

        # Ensure credentials have client_id and client_secret for refresh
        if creds.refresh_token and not creds.client_id:
            client_data = load_credentials_json()
            if client_data:
                # Create new credentials with client info
                creds = Credentials(
                    token=creds.token,
                    refresh_token=creds.refresh_token,
                    token_uri=creds.token_uri or "https://oauth2.googleapis.com/token",
                    client_id=client_data.get("client_id"),
                    client_secret=client_data.get("client_secret"),
                    scopes=creds.scopes,
                )

        return creds
    except Exception as e:
        logger.warning(f"Failed to load credentials for {user_id}: {e}")
        return None


def get_service_account_credentials(user_id: str) -> Credentials:
    """Get credentials using service account with domain-wide delegation.

    Args:
        user_id: User email address to impersonate

    Returns:
        Delegated credentials for the user
    """
    sa_file = find_service_account_file()
    if not sa_file:
        raise FileNotFoundError(
            "Service account file not found.\n"
            f"Checked: {SERVICE_ACCOUNT_FILE}, {CLAUDE_SERVICE_ACCOUNT_FILE}"
        )

    creds = service_account.Credentials.from_service_account_file(
        str(sa_file),
        scopes=GMAIL_SCOPES,
    )

    # Delegate to the target user
    return creds.with_subject(user_id)


def get_gmail_credentials(user_id: str, **kwargs) -> Credentials:
    """Get Gmail OAuth credentials for a user.

    Tries in order:
    1. Service account with domain-wide delegation (for any user)
    2. Per-user OAuth tokens (requires user has authenticated locally)

    Args:
        user_id: User email address

    Returns:
        Google OAuth Credentials object
    """
    # Try service account first (works for any domain user)
    sa_file = find_service_account_file()
    if sa_file:
        logger.debug(f"Using service account for {user_id}")
        try:
            return get_service_account_credentials(user_id)
        except Exception as e:
            logger.warning(f"Service account failed for {user_id}: {e}")

    # Try user's saved OAuth token
    creds = load_user_credentials(user_id)
    if creds:
        # Refresh if needed
        if creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                logger.debug(f"Refreshed credentials for {user_id}")
            except Exception as e:
                logger.warning(f"Failed to refresh credentials: {e}")
                creds = None

        if creds and creds.valid:
            return creds

    raise ValueError(
        f"No credentials found for {user_id}.\n"
        f"Either:\n"
        f"  1. Configure service account with domain-wide delegation, or\n"
        f"  2. Run 'gmail auth login --account {user_id}' to authenticate"
    )


def refresh_if_needed(credentials: Credentials) -> Credentials:
    """Refresh credentials if expired."""
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    return credentials
