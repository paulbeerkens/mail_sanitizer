import argparse
import sys
from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime
from pprint import pprint

from googleapiclient.discovery import build

from gmail_sanitizer import auth, client


def count_sender_frequencies(headlines):
    sender_counts = Counter(h.get("from", "") for h in headlines if h.get("from"))
    print("\nSender email frequency:")
    for email, count in sender_counts.most_common():
        print(f"{email}: {count}")


def format_date(value):
    if not value:
        return "(no date)"

    try:
        parsed = parsedate_to_datetime(value)
        if parsed is not None:
            return parsed.strftime("%Y%m%d")
    except (TypeError, ValueError):
        pass

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value

    return parsed.strftime("%Y%m%d")


def print_email_filtered_results(headlines, email_address):
    print(f"\nUnread messages from {email_address} ({len(headlines)} total):")
    for headline in headlines:
        subject = headline.get("subject") or "(no subject)"
        date = format_date(headline.get("date"))
        folder = headline.get("folder") or "(unknown folder)"
        labels = headline.get("labelIds") or []
        label_text = ", ".join(labels) if labels else "(no labelIds)"
        print(f"{date} — {subject} — {folder} — {label_text}")


def confirm_archive_prompt():
    try:
        confirmation = input("Archive these messages? [y/N]: ").strip().lower()
    except EOFError:
        return False
    return confirmation in {"y", "yes"}


def main():
    parser = argparse.ArgumentParser(description="Gmail sanitizer")
    parser.add_argument("--frequency", action="store_true", help="Count sender email frequencies for unread messages")
    parser.add_argument("--email", dest="email_address", help="Show unread messages from this email address")
    parser.add_argument("--all-email", dest="all_email_address", help="Show messages from this email address without requiring them to be unread")
    parser.add_argument("--archive-email", dest="archive_email_address", help="Archive unread messages from this email address")
    parser.add_argument("--max-results", type=int, default=50, help="Maximum number of unread messages to retrieve")
    args = parser.parse_args()

    try:
        creds = auth.get_credentials()
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)

    print("Granted Gmail scopes:")
    for scope in auth.get_granted_scopes(creds):
        print(f"- {scope}")

    service = build("gmail", "v1", credentials=creds)

    if args.all_email_address:
        headlines = client.list_messages_by_sender(service, args.all_email_address, max_results=args.max_results)
        if not headlines:
            print(f"No messages found for {args.all_email_address}.")
            return
        print_email_filtered_results(headlines, args.all_email_address)
        return

    if args.archive_email_address:
        headlines = client.list_unread_headlines_by_sender(service, args.archive_email_address, max_results=args.max_results)
        if not headlines:
            print(f"No unread messages found for {args.archive_email_address}.")
            return

        print_email_filtered_results(headlines, args.archive_email_address)

        if not confirm_archive_prompt():
            print("Archive cancelled.")
            return

        message_ids = [headline.get("id") for headline in headlines if headline.get("id")]
        archived_count = client.archive_messages(service, message_ids)

        print(f"Archived {archived_count} unread message(s) from {args.archive_email_address}.")
        return

    if args.email_address:
        headlines = client.list_unread_headlines_by_sender(service, args.email_address, max_results=args.max_results)
        if not headlines:
            print(f"No unread messages found for {args.email_address}.")
            return
        print_email_filtered_results(headlines, args.email_address)
        return

    headlines = client.list_unread_headlines(service, max_results=args.max_results)

    if not headlines:
        print("No unread messages found.")
        return

    if args.frequency:
        count_sender_frequencies(headlines)
    else:
        count_sender_frequencies(headlines)


if __name__ == "__main__":
    main()
