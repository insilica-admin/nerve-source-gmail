"""Gmail source adapter for Insilica Nerve."""

__version__ = "0.1.0"

from .source import GmailSource
from .auth import get_gmail_credentials

__all__ = ["GmailSource", "get_gmail_credentials"]
