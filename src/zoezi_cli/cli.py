from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
from pathlib import Path

from . import __version__, calendar, checkin
from .client import Client
from .errors import ZoeziError, redact
from .storage import read_json, state_lock, state_root


class Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ZoeziError("usage_error", message, exit_code=1)


def parser():
    p = Parser(prog="zoezi", description="JSON-first personal Zoezi bookings and QR check-in. No BankID or browser required.")
    p.add_argument("--version", action="version", version=__version__)
    p.add_argument("--url", help="Tenant URL (or ZOEZI_URL / saved setup)")
    p.add_argument("--timezone", help="IANA zone; default Europe/Stockholm")
    p.add_argument("--no-auto-login", action="store_true", help="Do not recover sessions using env/keychain credentials")
    commands = p.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("setup", help="Probe and configure the tenant without changing the account")
    setup.add_argument("--username")
    setup.add_argument("--calendar-id")
    commands.add_parser("config", help="Show local non-secret configuration")
    commands.add_parser("doctor", help="Inspect API, password login, queue and automation capabilities")
    commands.add_parser("status", help="Show member, upcoming booking counts and memberships")
    commands.add_parser("settings", help="Show redacted public facility settings")

    auth = commands.add_parser("auth").add_subparsers(dest="action", required=True)
    login = auth.add_parser("login")
    login.add_argument("--username", "-u")
    login.add_argument("--password-stdin", action="store_true")
    login.add_argument("--save-credentials", action="store_true", help="Save to the OS keychain for unattended re-login")
    auth.add_parser("status")
    auth.add_parser("refresh", help="Create a new session from env/keychain credentials")
    logout = auth.add_parser("logout")
    logout.add_argument("--forget-credentials", action="store_true")
    reset = auth.add_parser("reset-password", help="Request a reset email; does not change the password")
    reset.add_argument("--username", "-u")
    reset.add_argument("--dry-run", action="store_true")

    account = commands.add_parser("account").add_subparsers(dest="action", required=True)
    account.add_parser("fields", help="Discover the facility's registration/profile requirements")
    create = account.add_parser("create", help="Create your own member account, if enabled")
    create.add_argument("--data-file", required=True, help="JSON file or - for stdin; do not commit this file")
    create.add_argument("--dry-run", action="store_true")
    profile = commands.add_parser("profile")
    profile.add_argument("action", choices=["show", "update"], nargs="?", default="show")
    profile.add_argument("--data-file")
    profile.add_argument("--dry-run", action="store_true")
    memberships = commands.add_parser("memberships", aliases=["passes"])
    memberships.add_argument("--all", action="store_true")

    schedule = commands.add_parser("schedule")
    window_args(schedule, 7)
    schedule.add_argument("--name", help="Case-insensitive class/title substring")
    schedule.add_argument("--site", type=int)
    schedule.add_argument("--bookable-only", action="store_true")
    workout = commands.add_parser("workout")
    workout.add_argument("workout_id", type=int)

    bookings = commands.add_parser("bookings").add_subparsers(dest="action", required=True)
    listing = bookings.add_parser("list")
    window_args(listing, 31)
    for action in ("create", "cancel"):
        mutation_args(bookings.add_parser(action), cancel=action == "cancel")
    waitlist = commands.add_parser("waitlist").add_subparsers(dest="action", required=True)
    listing = waitlist.add_parser("list")
    window_args(listing, 31)
    for action in ("join", "leave", "claim"):
        mutation_args(waitlist.add_parser(action))

    entry = commands.add_parser("checkin", help="Inspect a scanned QR resource and register your own entry").add_subparsers(dest="action", required=True)
    for action in ("inspect", "qr"):
        sub = entry.add_parser(action, help="Read-only QR lookup" if action == "inspect" else "Register one QR check-in; may consume a punch")
        sub.add_argument("--qr-stdin", action="store_true", help="Read the scanned QR value from stdin instead of ZOEZI_QR_CODE / hidden prompt")
        if action == "qr":
            sub.add_argument("--dry-run", action="store_true", help="Resolve the resource and authenticate, without registering entry")
            sub.add_argument("--allow-punch", action="store_true", help="Acknowledge a possible or unknown training-card punch deduction")

    cal = commands.add_parser("calendar").add_subparsers(dest="action", required=True)
    for action in ("events", "export", "plan"):
        sub = cal.add_parser(action)
        window_args(sub, 31)
        sub.add_argument("--include-waitlist", action="store_true", help="Explicitly include tentative queue entries")
        if action == "export":
            sub.add_argument("--output", help="Write an ICS file instead of returning its contents in JSON")
        if action == "plan":
            sub.add_argument("--existing-file", required=True, help="JSON array from your calendar connector, or - for stdin")
    feed = cal.add_parser("feed", help="Inspect Zoezi's personal calendar subscription")
    feed.add_argument("--reveal", action="store_true", help="Explicitly return the private bearer URL")
    return p


def window_args(sub, days):
    sub.add_argument("--date", help="First local date YYYY-MM-DD")
    sub.add_argument("--days", type=int, default=days)


def mutation_args(sub, cancel=False):
    sub.add_argument("--workout", "--class", type=int, required=True, dest="workout_id")
    sub.add_argument("--dry-run", action="store_true")
    if cancel:
        sub.add_argument("--allow-late", action="store_true", help="Acknowledge a late or unknown cancellation policy")


def load_data(path):
    if not path:
        raise ZoeziError("usage_error", "Supply --data-file (or - for stdin).", exit_code=1)
    try:
        return json.load(sys.stdin) if path == "-" else json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ZoeziError("invalid_json", "Cannot read the JSON input.", exit_code=1) from exc


def qr_input(args):
    if args.qr_stdin:
        return sys.stdin.readline(4098).rstrip("\r\n")
    code = os.environ.get("ZOEZI_QR_CODE")
    if code is None:
        if not sys.stdin.isatty():
            raise ZoeziError("qr_code_required", "Use --qr-stdin or ZOEZI_QR_CODE for the scanned QR value.", exit_code=1)
        code = getpass.getpass("Scanned Zoezi QR value: ")
    return code


def run(args, client):
    command, action = args.command, getattr(args, "action", None)
    if command == "setup":
        return client.setup(args.username, args.calendar_id)
    if command == "doctor":
        return client.doctor()
    if command == "settings":
        return {"settings": client.settings()}
    if command == "auth":
        if action == "login":
            username = args.username or client.username
            if not username:
                raise ZoeziError("credentials_required", "Supply --username or ZOEZI_USERNAME.", exit_code=2)
            if args.password_stdin:
                password = sys.stdin.readline().rstrip("\r\n")
            else:
                password = os.environ.get("ZOEZI_PASSWORD")
                if password is None:
                    if not sys.stdin.isatty():
                        raise ZoeziError("credentials_required", "Use --password-stdin or ZOEZI_PASSWORD for unattended login.", exit_code=2)
                    password = getpass.getpass("Zoezi password: ")
            return client.login(username, password, save_credentials=args.save_credentials)
        if action == "logout":
            return client.logout(forget_credentials=args.forget_credentials)
        if action == "refresh":
            client.reauthenticate()
            return {"authenticated": True, "member": client.profile()}
        if action == "reset-password":
            return client.reset_password(args.username or client.username, dry_run=args.dry_run)
        return {"authenticated": True, "member": client.profile()}
    if command == "account":
        return client.fields() if action == "fields" else client.account_create(load_data(args.data_file), dry_run=args.dry_run)
    if command == "profile":
        return {"member": client.profile()} if action == "show" else client.profile_update(load_data(args.data_file), dry_run=args.dry_run)
    if command in ("memberships", "passes"):
        return {"memberships": client.memberships(all_cards=args.all)}
    if command == "status":
        member = client.profile()
        rows = client.bookings()
        return {"member": member, "facility": client.settings().get("name"),
                "confirmed_count": sum(row["booked"] and not row["cancelled"] for row in rows),
                "waitlist_count": sum(row["in_queue"] and not row["cancelled"] for row in rows),
                "memberships": client.memberships(), "window_days": 31}
    if command == "schedule":
        rows = client.schedule(start=args.date, days=args.days, name=args.name, site=args.site, bookable_only=args.bookable_only)
        return {"workouts": rows, "count": len(rows), "timezone": str(client.tz)}
    if command == "workout":
        return {"workout": client.workout(args.workout_id)}
    if command == "checkin":
        code = qr_input(args)
        if action == "inspect":
            return checkin.inspect_qr(client, code)
        return checkin.submit_qr(client, code, dry_run=args.dry_run, allow_punch=args.allow_punch)
    if command in ("bookings", "waitlist"):
        if action == "list":
            rows = client.bookings(start=args.date, days=args.days)
            confirmed = [row for row in rows if row["booked"] and not row["cancelled"]]
            queued = [row for row in rows if row["in_queue"] and not row["cancelled"]]
            return {"bookings": confirmed, "waitlist": queued, "cancelled": [row for row in rows if row["cancelled"]]} if command == "bookings" else {"waitlist": queued, "count": len(queued)}
        return client.mutate_booking(action, args.workout_id, dry_run=args.dry_run, allow_late=getattr(args, "allow_late", False))
    if command == "calendar":
        if action == "feed":
            from urllib.parse import urlencode

            member = client.request("GET", "/api/memberapi/get/current")
            calendar_id = member.get("icalendarid")
            return {"available": bool(calendar_id), "private_url": client.tenant + "/api/public/icalendar/get?" + urlencode({"id": calendar_id}) if calendar_id and args.reveal else None,
                    "note": "Treat the revealed URL as a secret; never put it in shared event descriptions."}
        kwargs = {"start": args.date, "days": args.days, "include_waitlist": args.include_waitlist}
        if action == "plan":
            return calendar.plan(client, load_data(args.existing_file), **kwargs)
        event_list = calendar.events(client, **kwargs)
        if action == "events":
            return {"events": event_list, "calendar_id": client.config.get("calendar_id")}
        content = calendar.ics(event_list)
        if args.output:
            Path(args.output).write_bytes(content.encode())
            return {"output": str(Path(args.output).resolve()), "count": len(event_list)}
        return {"icalendar": content, "count": len(event_list)}
    raise ZoeziError("usage_error", "Unknown command.", exit_code=1)


def main():
    client = None
    try:
        args = parser().parse_args()
        root = state_root()
        with state_lock(root):
            if args.command == "config":
                result = {"config": read_json(root / "config.json", {}), "config_dir": str(root)}
            else:
                client = Client(root=root, tenant=args.url, tz=args.timezone, auto_login=not args.no_auto_login)
                result = run(args, client)
            output = {"ok": True, **result}
            print(json.dumps(client.safe(output) if client else redact(output), ensure_ascii=False))
    except ZoeziError as exc:
        print(json.dumps(client.safe(exc.as_dict()) if client else redact(exc.as_dict(), [os.environ.get("ZOEZI_PASSWORD"), os.environ.get("ZOEZI_QR_CODE")]), ensure_ascii=False), file=sys.stderr)
        raise SystemExit(exc.exit_code)
    except KeyboardInterrupt:
        print(json.dumps({"ok": False, "error": "Interrupted; verify the operation before repeating a write. For check-in, check the app or reception.", "code": "interrupted", "details": {}}), file=sys.stderr)
        raise SystemExit(130)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        print(json.dumps({"ok": False, "error": "Local input or API response could not be processed.", "code": "invalid_data", "details": {"exception_type": type(exc).__name__}}), file=sys.stderr)
        raise SystemExit(1)
