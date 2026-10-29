import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from zoezi_cli import calendar
from zoezi_cli.cli import main, parser, run
from zoezi_cli.client import Client
from zoezi_cli.errors import ZoeziError

from test_client import FakeTransport, NOW, TENANT, workout


class CalendarTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.transport = FakeTransport()
        self.client = Client(root=Path(self.temp.name), tenant=TENANT, transport=self.transport, now=lambda: NOW)

    def tearDown(self):
        self.temp.cleanup()

    def test_default_export_excludes_waitlist(self):
        self.transport.own[102] = {**workout(102, "04"), "booked": True, "inQueue": True}
        confirmed = calendar.events(self.client)
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["status"], "CONFIRMED")
        tentative = calendar.events(self.client, include_waitlist=True)
        self.assertEqual(tentative[1]["status"], "TENTATIVE")

    def test_stable_source_keys_include_member_and_tenant(self):
        event = calendar.events(self.client)[0]
        self.assertEqual(event["source_key"], "zoezi:example.zoezi.se:100:101")
        self.assertEqual(event["uid"], calendar.events(self.client)[0]["uid"])

    def test_plan_does_not_delete_unrelated_or_outside_window(self):
        events = calendar.events(self.client)
        old = {**events[0], "source_key": "zoezi:example.zoezi.se:100:999", "event_id": "old"}
        outside = {**old, "start": "2026-12-01T17:00:00+01:00", "source_key": "zoezi:example.zoezi.se:100:998"}
        unrelated = {**old, "source_key": "family:dinner", "event_id": "dinner"}
        result = calendar.plan(self.client, [old, outside, unrelated])
        self.assertEqual([r["action"] for r in result["changes"]], ["create", "delete"])
        self.assertEqual(result["changes"][1]["event_id"], "old")

    def test_plan_no_duplicate_and_updates_changes(self):
        event = calendar.events(self.client)[0]
        self.assertEqual(calendar.plan(self.client, [event])["changes"], [])
        event["summary"] = "Old title"
        event["event_id"] = "existing-id"
        self.assertEqual(calendar.plan(self.client, [event])["changes"][0]["event_id"], "existing-id")

    def test_ics_escaping_unicode_folding_and_dst(self):
        event = calendar.events(self.client)[0]
        event["summary"] = "Träning; " + "å" * 80 + ",\nTest"
        content = calendar.ics([event], now=datetime(2026, 10, 29, tzinfo=timezone.utc))
        self.assertIn("DTSTART:20261103T160000Z", content)
        self.assertIn("\\;", content)
        self.assertIn("\\,\\nTest", content)
        self.assertTrue(all(len(line.encode()) <= 75 for line in content.split("\r\n")))

    def test_feed_requires_explicit_reveal(self):
        self.transport.member["icalendarid"] = "synthetic-private-id"
        hidden = run(parser().parse_args(["calendar", "feed"]), self.client)
        self.assertTrue(hidden["available"])
        self.assertIsNone(hidden["private_url"])
        revealed = run(parser().parse_args(["calendar", "feed", "--reveal"]), self.client)
        self.assertIn("synthetic-private-id", revealed["private_url"])


class CliTests(unittest.TestCase):
    def test_invalid_arguments_emit_json_stderr(self):
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.argv", ["zoezi", "not-a-command"]), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit) as err:
                main()
        self.assertEqual(err.exception.code, 1)
        self.assertEqual(json.loads(stderr.getvalue())["code"], "usage_error")
        self.assertEqual(stdout.getvalue(), "")

    def test_logged_out_read_fails_with_documented_exit(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict(os.environ, {"ZOEZI_CONFIG_DIR": tmp}, clear=True):
            stderr = io.StringIO()
            with patch("sys.argv", ["zoezi", "--url", TENANT, "bookings", "list"]), contextlib.redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as err:
                    main()
            self.assertEqual(err.exception.code, 2)
            self.assertEqual(json.loads(stderr.getvalue())["code"], "login_required")

    def test_all_documented_mutations_support_dry_run(self):
        for command, action in [("bookings", "create"), ("bookings", "cancel"), ("waitlist", "join"), ("waitlist", "leave"), ("waitlist", "claim")]:
            args = parser().parse_args([command, action, "--workout", "102", "--dry-run"])
            self.assertTrue(args.dry_run)

    def test_bankid_and_password_argument_do_not_exist(self):
        for arguments in [["auth", "login", "--bankid"], ["auth", "login", "--password", "unsafe"]]:
            with self.assertRaises(ZoeziError):
                parser().parse_args(arguments)


if __name__ == "__main__":
    unittest.main()
