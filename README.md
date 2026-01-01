# nerve-source-gmail

Gmail source adapter for Insilica Nerve. Syncs emails from Gmail API and publishes events to Pub/Sub.

## Installation

```bash
pip install -e .
```

Requires `insilica-nerve` to be installed.

## Usage

```bash
# Test Gmail access
nerve-source-gmail test tom@insilica.co

# Sync emails (last 7 days by default)
nerve-source-gmail sync tom@insilica.co

# Sync since specific date
nerve-source-gmail sync tom@insilica.co --since 2024-01-01

# Sync all emails
nerve-source-gmail sync tom@insilica.co --all --max 1000

# Watch for new emails (daemon mode)
nerve-source-gmail watch tom@insilica.co --interval 60
```

## Environment Variables

```bash
# Required
GCP_PROJECT=insilica-quickbooks

# Auth service (for OAuth credentials)
INSILICA_AUTH_URL=http://10.0.0.39:8000
```

## How It Works

1. Gets OAuth credentials from `insilica-auth` service
2. Fetches emails from Gmail API
3. Converts each email to a `NerveEvent`
4. Publishes to `nerve-source-gmail` Pub/Sub topic

## Event Schema

Each email becomes a NerveEvent:

```python
NerveEvent(
    source="gmail",
    source_id="message_id",
    user_id="tom@insilica.co",
    event_type="email_received",  # or "email_sent"
    timestamp=email_date,
    title=subject,
    content=body_text,
    thread_id="thread_id",
    metadata={
        "from": "sender@example.com",
        "to": "recipient@example.com",
        "cc": "cc@example.com",
        "labels": ["INBOX", "UNREAD"],
        "has_attachments": True,
        "snippet": "Preview text...",
    }
)
```

## Downstream

Events are consumed by:
- `nerve-map-classifier` - Adds AI classification
- Which then publishes to:
  - `nerve-user-{user}` - Full content for user's private DB
  - `nerve-shared` - Sanitized summary for org visibility
