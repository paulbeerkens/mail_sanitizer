import argparse
import logging
import sys
from collections import Counter
from datetime import datetime
from email.utils import parsedate_to_datetime
from pprint import pprint

from googleapiclient.discovery import build

from gmail_sanitizer import auth, client, database


def store_messages(account_email, headlines):
    return database.upsert_messages(account_email, headlines)


def count_sender_frequencies(headlines):
    sender_counts = Counter(h.get("from", "") for h in headlines if h.get("from"))
    print("\nSender email frequency:")
    for email, count in sender_counts.most_common():
        print(f"{email}: {count}")


def print_database_sender_counts(sender_counts, account_email):
    print(f"\nUnread Promotions message counts for {account_email}:")
    for email, count in sender_counts:
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


def print_email_filtered_results(headlines, email_address, unread_only=True):
    message_kind = "Unread messages" if unread_only else "Messages"
    print(f"\n{message_kind} from {email_address} ({len(headlines)} total):")
    for headline in headlines:
        subject = headline.get("subject") or "(no subject)"
        date = format_date(headline.get("date"))
        folder = headline.get("folder") or "(unknown folder)"
        labels = headline.get("labelIds") or []
        label_text = ", ".join(labels) if labels else "(no labelIds)"
        print(f"{date} — {subject} — {folder} — {label_text}")


def confirm_archive_prompt(message_count):
    try:
        confirmation = input(
            f"Trash these messages ({message_count} total)? [y/N]: "
        ).strip().lower()
    except EOFError:
        return False
    return confirmation in {"y", "yes"}


def main():
    parser = argparse.ArgumentParser(description="Gmail sanitizer")
    commands = parser.add_argument_group("main commands")
    commands.add_argument(
        "--count",
        action="store_true",
        help="Count unread Promotions messages by sender using SQLite",
    )
    commands.add_argument(
        "--email",
        dest="email_address",
        help="Show unread Promotions messages from this email address",
    )
    commands.add_argument(
        "--all-email",
        dest="all_email_address",
        help="Show read and unread messages from this email address",
    )
    archive_group = commands.add_mutually_exclusive_group()
    archive_group.add_argument(
        "--archive-email",
        dest="archive_email_address",
        help="Trash unread Promotions messages selected from SQLite for this sender",
    )
    archive_group.add_argument(
        "--archive-all-promotional-email",
        dest="archive_all_promotional_email_address",
        help="Trash read and unread Promotions messages selected from SQLite for this sender",
    )
    commands.add_argument(
        "--database-dump",
        action="store_true",
        help="Retrieve Gmail messages and update the local SQLite database",
    )

    modifiers = parser.add_argument_group("command modifiers")
    modifiers.add_argument(
        "--in-anywhere",
        action="store_true",
        help="With --all-email, include Spam and Trash",
    )
    modifiers.add_argument(
        "--expand-history",
        action="store_true",
        help="With --database-dump, retrieve messages at or before the oldest database record",
    )
    modifiers.add_argument(
        "--category",
        choices=client.DATABASE_DUMP_CATEGORIES,
        default="promotions",
        help="With --database-dump, select a Gmail category (default: promotions)",
    )
    modifiers.add_argument(
        "--unread-only",
        action="store_true",
        help="With --database-dump, retrieve only unread messages",
    )
    modifiers.add_argument(
        "--include-archived",
        action="store_true",
        help="With --database-dump, include messages outside the Inbox",
    )
    modifiers.add_argument(
        "--account-email",
        help="With --count, select the Gmail account stored in SQLite",
    )

    request_settings = parser.add_argument_group("request settings")
    request_settings.add_argument(
        "--max-results",
        type=int,
        default=50,
        help="Maximum total number of messages to retrieve (default: 50)",
    )
    request_settings.add_argument(
        "--page-delay",
        type=float,
        default=client.DEFAULT_PAGE_DELAY_SECONDS,
        metavar="SECONDS",
        help=f"Delay between Gmail result pages (default: {client.DEFAULT_PAGE_DELAY_SECONDS} seconds)",
    )
    request_settings.add_argument(
        "--trash-delay",
        type=float,
        default=client.DEFAULT_TRASH_DELAY_SECONDS,
        metavar="SECONDS",
        help=f"Delay between individual Trash operations (default: {client.DEFAULT_TRASH_DELAY_SECONDS} seconds)",
    )

    output_options = parser.add_argument_group("output options")
    output_options.add_argument(
        "--no-query-logging",
        action="store_true",
        help="Do not print Gmail search queries or SQLite statements",
    )
    args = parser.parse_args()

    query_logger = logging.getLogger("gmail_sanitizer.query")
    query_logger.propagate = False
    if not query_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("QUERY %(message)s"))
        query_logger.addHandler(handler)
    query_logger.setLevel(
        logging.CRITICAL + 1 if args.no_query_logging else logging.INFO
    )

    if args.page_delay < 0:
        parser.error("--page-delay must be zero or greater")
    if args.trash_delay < 0:
        parser.error("--trash-delay must be zero or greater")
    if args.expand_history and not args.database_dump:
        parser.error("--expand-history requires --database-dump")
    if args.category != "promotions" and not args.database_dump:
        parser.error("--category requires --database-dump")
    if args.unread_only and not args.database_dump:
        parser.error("--unread-only requires --database-dump")
    if args.include_archived and not args.database_dump:
        parser.error("--include-archived requires --database-dump")
    if args.in_anywhere and not args.all_email_address:
        parser.error("--in-anywhere requires --all-email")
    if args.account_email and not args.count:
        parser.error("--account-email requires --count")

    if args.count:
        account_email = args.account_email
        database_accounts = database.get_account_emails()
        if account_email is None:
            if not database_accounts:
                print("No messages are stored in the local database.")
                return
            if len(database_accounts) > 1:
                parser.error(
                    "--count found multiple accounts in SQLite; use "
                    "--account-email with one of: " + ", ".join(database_accounts)
                )
            account_email = database_accounts[0]
        elif account_email not in database_accounts:
            parser.error(
                f"no SQLite messages found for account {account_email!r}"
            )

        sender_counts = database.count_unread_promotions_by_sender(account_email)
        print_database_sender_counts(sender_counts, account_email)
        return

    try:
        creds = auth.get_credentials()
    except FileNotFoundError as e:
        print(e)
        sys.exit(1)

    print("Granted Gmail scopes:")
    for scope in auth.get_granted_scopes(creds):
        print(f"- {scope}")

    service = build("gmail", "v1", credentials=creds)
    account_email = service.users().getProfile(userId="me").execute()["emailAddress"]

    def load_cached_metadata(message_ids):
        return database.get_cached_message_metadata(account_email, message_ids)

    if args.database_dump:
        before_epoch_seconds = None
        if args.expand_history:
            oldest_internal_date_ms = database.get_oldest_internal_date_ms(
                account_email,
                category=args.category,
                unread_only=args.unread_only,
                include_archived=args.include_archived,
            )
            if oldest_internal_date_ms is None:
                parser.error(
                    "--expand-history requires existing database records with Gmail internal dates"
                )
            # before: is exclusive and uses whole seconds. Add one second to
            # include the existing boundary; the database upsert removes duplicates.
            before_epoch_seconds = oldest_internal_date_ms // 1000 + 1

        stored_count = 0

        def store_page(page):
            nonlocal stored_count
            stored_count += store_messages(account_email, page)

        client.list_messages_for_database_dump(
            service,
            max_results=args.max_results,
            page_delay_seconds=args.page_delay,
            before_epoch_seconds=before_epoch_seconds,
            page_callback=store_page,
            category=args.category,
            unread_only=args.unread_only,
            include_archived=args.include_archived,
        )
        print(
            f"Updated {stored_count} {args.category} message(s) "
            "in the local database."
        )
        return

    if args.all_email_address:
        headlines = client.list_messages_by_sender(
            service,
            args.all_email_address,
            max_results=args.max_results,
            page_delay_seconds=args.page_delay,
            metadata_provider=load_cached_metadata,
            in_anywhere=args.in_anywhere,
        )
        store_messages(account_email, headlines)
        if not headlines:
            print(f"No messages found for {args.all_email_address}.")
            return
        print_email_filtered_results(
            headlines,
            args.all_email_address,
            unread_only=False,
        )
        return

    archive_email_address = (
        args.archive_email_address or args.archive_all_promotional_email_address
    )
    if archive_email_address:
        unread_only = args.archive_email_address is not None
        headlines = database.list_promotions_by_sender(
            account_email,
            archive_email_address,
            unread_only=unread_only,
            max_results=args.max_results,
        )
        print(f"Loaded {len(headlines)} matching message(s) from the database.")
        if not headlines:
            message_kind = "unread messages" if unread_only else "messages"
            print(f"No {message_kind} found for {archive_email_address}.")
            return

        print_email_filtered_results(
            headlines,
            archive_email_address,
            unread_only=unread_only,
        )

        if not confirm_archive_prompt(len(headlines)):
            print("Archive cancelled.")
            return

        message_ids = [headline.get("id") for headline in headlines if headline.get("id")]
        def record_trash_progress(message_id, trashed_message, completed, total):
            database.mark_message_trashed(
                account_email,
                message_id,
                label_ids=trashed_message.get("labelIds"),
            )
            current_time = datetime.now().astimezone().isoformat(timespec="seconds")
            print(f"[{current_time}] trashed: {completed}/{total}")

        archived_count = client.archive_messages(
            service,
            message_ids,
            trash_delay_seconds=args.trash_delay,
            progress_callback=record_trash_progress,
        )

        message_kind = "unread message(s)" if unread_only else "message(s)"
        print(f"Trashed {archived_count} {message_kind} from {archive_email_address}.")

        if args.archive_all_promotional_email_address:
            print(
                f"\nChecking for messages from {archive_email_address} "
                "that remain outside Spam and Trash..."
            )
            remaining = client.list_messages_by_sender(
                service,
                archive_email_address,
                max_results=args.max_results,
                page_delay_seconds=args.page_delay,
                metadata_provider=load_cached_metadata,
                in_anywhere=False,
            )
            store_messages(account_email, remaining)
            if remaining:
                print_email_filtered_results(
                    remaining,
                    archive_email_address,
                    unread_only=False,
                )
            else:
                print(
                    f"No messages from {archive_email_address} remain outside "
                    "Spam and Trash."
                )
        return

    if args.email_address:
        headlines = client.list_unread_headlines_by_sender(
            service,
            args.email_address,
            max_results=args.max_results,
            page_delay_seconds=args.page_delay,
            metadata_provider=load_cached_metadata,
        )
        store_messages(account_email, headlines)
        if not headlines:
            print(f"No unread messages found for {args.email_address}.")
            return
        print_email_filtered_results(headlines, args.email_address)
        return

    headlines = client.list_unread_headlines(
        service,
        max_results=args.max_results,
        page_delay_seconds=args.page_delay,
        metadata_provider=load_cached_metadata,
    )
    store_messages(account_email, headlines)

    if not headlines:
        print("No unread messages found.")
        return

    count_sender_frequencies(headlines)


if __name__ == "__main__":
    main()
