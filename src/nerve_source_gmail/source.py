"""Gmail source adapter for Insilica Nerve.

Syncs emails from Gmail API and writes events to the event store.
"""

import base64
import logging
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Iterator, Optional

from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from insilica_nerve import NerveSource, NerveEvent, NerveConfig
from insilica_nerve.events import EventType

from .auth import get_gmail_credentials, refresh_if_needed

logger = logging.getLogger(__name__)


def decode_body(data: str) -> str:
    """Decode base64url encoded email body."""
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
    except Exception:
        return ""


def get_body_text(payload: dict) -> str:
    """Extract plain text body from email payload."""
    if not payload:
        return ""

    # Check for direct body
    if payload.get("body", {}).get("data"):
        return decode_body(payload["body"]["data"])

    # Check parts
    for part in payload.get("parts", []):
        mime_type = part.get("mimeType", "")

        if mime_type == "text/plain":
            return decode_body(part.get("body", {}).get("data", ""))

        # Recurse into multipart
        if mime_type.startswith("multipart/"):
            result = get_body_text(part)
            if result:
                return result

    # Fall back to HTML
    for part in payload.get("parts", []):
        if part.get("mimeType") == "text/html":
            html = decode_body(part.get("body", {}).get("data", ""))
            # Strip HTML tags (basic)
            return re.sub(r"<[^>]+>", "", html)

    return ""


def get_header(headers: list, name: str) -> str:
    """Get a header value by name."""
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def parse_email_addresses(header_value: str) -> list[str]:
    """Parse email addresses from a header value."""
    if not header_value:
        return []
    # Simple regex for email extraction
    return re.findall(r"[\w\.-]+@[\w\.-]+", header_value)


class GmailSource(NerveSource):
    """Gmail source adapter.

    Syncs emails from Gmail API and writes to the nerve event store.
    """

    source_name = "gmail"

    def __init__(self, config: NerveConfig):
        super().__init__(config)
        self._services: dict[str, any] = {}

    def get_service(self, user_id: str):
        """Get Gmail API service for a user."""
        if user_id not in self._services:
            credentials = get_gmail_credentials(user_id)
            credentials = refresh_if_needed(credentials)
            self._services[user_id] = build("gmail", "v1", credentials=credentials)
        return self._services[user_id]

    def fetch_events(
        self,
        user_id: str,
        since: Optional[str] = None,
        max_results: int = 100,
    ) -> Iterator[NerveEvent]:
        """Fetch email events for a user.

        Args:
            user_id: User email address
            since: History ID or date to fetch from
            max_results: Maximum emails to fetch

        Yields:
            NerveEvent for each email
        """
        service = self.get_service(user_id)

        # Build query
        query = ""
        if since:
            # If since looks like a date, use after:
            if re.match(r"\d{4}-\d{2}-\d{2}", since):
                query = f"after:{since.replace('-', '/')}"
            # Otherwise assume it's a history ID and we'll use history API

        try:
            # List messages
            results = service.users().messages().list(
                userId="me",
                q=query,
                maxResults=max_results,
            ).execute()

            messages = results.get("messages", [])
            logger.info(f"Found {len(messages)} messages for {user_id}")

            for msg_ref in messages:
                try:
                    event = self._fetch_message(service, user_id, msg_ref["id"])
                    if event:
                        yield event
                except HttpError as e:
                    logger.warning(f"Error fetching message {msg_ref['id']}: {e}")
                    continue

        except HttpError as e:
            logger.error(f"Error listing messages for {user_id}: {e}")
            raise

    def _fetch_message(self, service, user_id: str, message_id: str) -> Optional[NerveEvent]:
        """Fetch a single message and convert to NerveEvent."""
        msg = service.users().messages().get(
            userId="me",
            id=message_id,
            format="full",
        ).execute()

        payload = msg.get("payload", {})
        headers = payload.get("headers", [])

        # Extract headers
        subject = get_header(headers, "Subject") or "(no subject)"
        from_addr = get_header(headers, "From")
        to_addr = get_header(headers, "To")
        cc_addr = get_header(headers, "Cc")
        date_str = get_header(headers, "Date")

        # Parse date
        try:
            if date_str:
                timestamp = parsedate_to_datetime(date_str)
            else:
                # Use internal date
                timestamp = datetime.fromtimestamp(int(msg["internalDate"]) / 1000)
        except Exception:
            timestamp = datetime.now()

        # Determine event type
        labels = msg.get("labelIds", [])
        if "SENT" in labels:
            event_type = EventType.EMAIL_SENT.value
        else:
            event_type = EventType.EMAIL_RECEIVED.value

        # Extract body
        body_text = get_body_text(payload)
        snippet = msg.get("snippet", "")

        # Check for attachments
        has_attachments = False
        attachment_ids = []
        for part in payload.get("parts", []):
            if part.get("filename"):
                has_attachments = True
                if part.get("body", {}).get("attachmentId"):
                    attachment_ids.append(part["body"]["attachmentId"])

        return NerveEvent(
            source="gmail",
            source_id=message_id,
            user_id=user_id,
            timestamp=timestamp,
            event_type=event_type,
            title=subject,
            content=body_text or snippet,
            thread_id=msg.get("threadId"),
            metadata={
                "from": from_addr,
                "to": to_addr,
                "cc": cc_addr,
                "to_addresses": parse_email_addresses(to_addr),
                "cc_addresses": parse_email_addresses(cc_addr),
                "labels": labels,
                "snippet": snippet,
                "has_attachments": has_attachments,
                "attachment_ids": attachment_ids,
                "history_id": msg.get("historyId"),
            },
        )

    def sync_incremental(self, user_id: str, history_id: str) -> Iterator[NerveEvent]:
        """Sync incrementally using Gmail history API.

        Args:
            user_id: User email address
            history_id: Last known history ID

        Yields:
            NerveEvent for each new/modified email
        """
        service = self.get_service(user_id)

        try:
            results = service.users().history().list(
                userId="me",
                startHistoryId=history_id,
                historyTypes=["messageAdded", "messageDeleted"],
            ).execute()

            history = results.get("history", [])

            # Collect message IDs
            message_ids = set()
            for record in history:
                for msg in record.get("messagesAdded", []):
                    message_ids.add(msg["message"]["id"])

            logger.info(f"Found {len(message_ids)} new messages via history API")

            for msg_id in message_ids:
                try:
                    event = self._fetch_message(service, user_id, msg_id)
                    if event:
                        yield event
                except HttpError as e:
                    logger.warning(f"Error fetching message {msg_id}: {e}")

        except HttpError as e:
            if e.resp.status == 404:
                # History expired, need full sync
                logger.warning("History ID expired, need full sync")
                raise ValueError("History ID expired")
            raise
