"""CLI for Gmail source adapter."""

import argparse
import logging
import os
import signal
import sys
import time
from datetime import datetime, timedelta

from insilica_nerve import NerveConfig

from .source import GmailSource

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def cmd_sync(args):
    """Sync emails for a user."""
    config = NerveConfig.from_env()

    if not config.gcp_project:
        print("Error: GCP_PROJECT environment variable required", file=sys.stderr)
        sys.exit(1)

    source = GmailSource(config)

    # Default to last 7 days if no since specified
    since = args.since
    if not since and not args.all:
        since = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

    print(f"Syncing Gmail for {args.user}")
    if since:
        print(f"  Since: {since}")
    print(f"  Max: {args.max} messages")
    print()

    count = 0
    for event in source.fetch_events(args.user, since=since, max_results=args.max):
        source.publish(event)
        count += 1

        if not args.quiet:
            ts = event.timestamp.strftime("%Y-%m-%d %H:%M")
            subject = event.title[:50]
            print(f"  [{ts}] {subject}")

    print(f"\nPublished {count} events to event store")


def cmd_watch(args):
    """Watch for new emails and publish continuously."""
    config = NerveConfig.from_env()

    if not config.gcp_project:
        print("Error: GCP_PROJECT environment variable required", file=sys.stderr)
        sys.exit(1)

    source = GmailSource(config)

    print(f"Watching Gmail for {args.user}")
    print(f"  Poll interval: {args.interval}s")
    print("  Press Ctrl+C to stop\n")

    running = True

    def shutdown(signum, frame):
        nonlocal running
        print("\nShutting down...")
        running = False

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    # Track what we've seen
    last_check = datetime.now() - timedelta(minutes=5)

    while running:
        try:
            since = last_check.strftime("%Y-%m-%d")
            count = 0

            for event in source.fetch_events(args.user, since=since, max_results=50):
                # Only publish if newer than last check
                if event.timestamp > last_check:
                    source.publish(event)
                    count += 1

                    ts = event.timestamp.strftime("%H:%M")
                    subject = event.title[:50]
                    print(f"  [{ts}] {subject}")

            if count > 0:
                print(f"  Published {count} new events")

            last_check = datetime.now()

        except Exception as e:
            logger.error(f"Error during sync: {e}")

        # Wait for next poll
        for _ in range(args.interval):
            if not running:
                break
            time.sleep(1)


def cmd_test(args):
    """Test Gmail API access."""
    config = NerveConfig.from_env()
    source = GmailSource(config)

    print(f"Testing Gmail access for {args.user}...")

    try:
        service = source.get_service(args.user)

        # Get profile
        profile = service.users().getProfile(userId="me").execute()
        print(f"  Email: {profile['emailAddress']}")
        print(f"  Total messages: {profile['messagesTotal']}")
        print(f"  Total threads: {profile['threadsTotal']}")

        # Get recent message
        results = service.users().messages().list(
            userId="me",
            maxResults=1,
        ).execute()

        if results.get("messages"):
            msg_id = results["messages"][0]["id"]
            msg = service.users().messages().get(
                userId="me",
                id=msg_id,
                format="metadata",
                metadataHeaders=["Subject", "From", "Date"],
            ).execute()

            headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
            print(f"\n  Latest email:")
            print(f"    Subject: {headers.get('Subject', 'N/A')}")
            print(f"    From: {headers.get('From', 'N/A')}")
            print(f"    Date: {headers.get('Date', 'N/A')}")

        print("\nGmail access OK!")

    except Exception as e:
        print(f"\nError: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Gmail source for Insilica Nerve")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Sync
    sync_parser = subparsers.add_parser("sync", help="Sync emails to event store")
    sync_parser.add_argument("user", help="User email address")
    sync_parser.add_argument("--since", "-s", help="Sync since date (YYYY-MM-DD)")
    sync_parser.add_argument("--all", "-a", action="store_true", help="Sync all messages")
    sync_parser.add_argument("--max", "-m", type=int, default=100, help="Max messages")
    sync_parser.add_argument("--quiet", "-q", action="store_true", help="Quiet output")

    # Watch
    watch_parser = subparsers.add_parser("watch", help="Watch for new emails")
    watch_parser.add_argument("user", help="User email address")
    watch_parser.add_argument("--interval", "-i", type=int, default=60, help="Poll interval (seconds)")

    # Test
    test_parser = subparsers.add_parser("test", help="Test Gmail access")
    test_parser.add_argument("user", help="User email address")

    args = parser.parse_args()

    commands = {
        "sync": cmd_sync,
        "watch": cmd_watch,
        "test": cmd_test,
    }

    commands[args.command](args)


if __name__ == "__main__":
    main()
