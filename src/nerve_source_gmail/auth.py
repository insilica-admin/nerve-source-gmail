"""Authentication for Gmail API access.

Gets OAuth credentials from the insilica-auth service.
"""

import logging
import os
from typing import Optional

import httpx
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
]


def get_gmail_credentials(
    user_id: str,
    auth_service_url: Optional[str] = None,
) -> Credentials:
    """Get Gmail OAuth credentials for a user.

    Args:
        user_id: User email address
        auth_service_url: URL of insilica-auth service

    Returns:
        Google OAuth Credentials object
    """
    auth_url = auth_service_url or os.getenv("INSILICA_AUTH_URL", "http://10.0.0.39:8000")

    with httpx.Client(timeout=30.0) as client:
        response = client.get(
            f"{auth_url}/api/credentials/google",
            params={"user_id": user_id},
        )
        response.raise_for_status()
        data = response.json()

    if "credentials" not in data:
        raise ValueError(f"No credentials found for {user_id}")

    creds_data = data["credentials"]

    return Credentials(
        token=creds_data.get("token"),
        refresh_token=creds_data.get("refresh_token"),
        token_uri=creds_data.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id=creds_data.get("client_id"),
        client_secret=creds_data.get("client_secret"),
        scopes=creds_data.get("scopes", GMAIL_SCOPES),
    )


def refresh_if_needed(credentials: Credentials) -> Credentials:
    """Refresh credentials if expired."""
    if credentials.expired and credentials.refresh_token:
        from google.auth.transport.requests import Request
        credentials.refresh(Request())
    return credentials
