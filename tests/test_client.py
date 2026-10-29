import copy
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

from zoezi_cli.client import Client, tenant_url
from zoezi_cli.errors import ZoeziError, redact
from zoezi_cli.storage import read_json, state_lock, write_json


TENANT = "https://example.zoezi.se"
NOW = datetime(2026, 10, 29, 12, tzinfo=ZoneInfo("Europe/Stockholm"))


def workout(number=101, day="03", capacity=10, booked=2, queued=0):
    return {"id": number, "startTime": f"2026-11-{day} 17:00:00", "endTime": f"2026-11-{day} 18:00:00",
            "workoutType": {"name": "Group Class", "category_id": 2}, "status": "Ok", "site_id": 1,
            "space": capacity, "numBooked": booked, "numQueue": queued, "bookableForCustomer": True,
            "resources": [{"name": "Main room"}], "staffs": [], "booked": False, "inQueue": False}


class FakeTransport:
    def __init__(self):
        self.token = "synthetic-session"
        self.calls = []
        self.rows = {101: workout(), 102: workout(102, "04")}
        self.own = {101: {**workout(), "booked": True}}
        self.settings = {"name": "Example Box", "login": {"mail": True}, "canQueue": True, "workoutbooking": True,
                         "canCreate": True, "version": "11.4", "workoutcategories": [{"id": 2, "registerDaysBefore": 30,
                         "registerHoursBefore": 0, "unregisterHoursBefore": 2, "firstCome": True}]}
        self.member = {"id": 100, "firstname": "Example", "lastname": "Member", "mail": "member@example.test", "session": "synthetic-session"}
        self.error_after_write = False
        self.failed_readback = False
        self.fail_auth_once = False
        self.forbidden = False
        self.promote_during_join = False

    def session(self):
        return self.token

    def set_session(self, token):
        self.token = token

    def request(self, method, path, *, params=None, body=None, text=False):
        self.calls.append((method, path, params, body))
        if path == "/api/public/settings/get":
            return copy.deepcopy(self.settings)
        if path == "/api/v8.0/memberapi/login":
            if body["password"] != "test-password":
                raise ZoeziError("auth_rejected", "Invalid password", exit_code=2)
            self.token = "new-synthetic-session"
            return copy.deepcopy(self.member)
        if path == "/api/memberapi/add":
            return copy.deepcopy(self.member)
        if self.forbidden:
            raise ZoeziError("permission_denied", "Not allowed", exit_code=2)
        if self.fail_auth_once:
            self.fail_auth_once = False
            raise ZoeziError("auth_rejected", "Expired", exit_code=2)
        if path == "/api/memberapi/get/current":
            return copy.deepcopy(self.member)
        if path == "/api/memberapi/bookings/get":
            if self.failed_readback:
                raise ZoeziError("network_error", "Unavailable", exit_code=4)
            return {"workouts": copy.deepcopy(list(self.own.values()))}
        if path == "/api/public/workout/get/all":
            return {"workouts": copy.deepcopy(list(self.rows.values()))}
        if path == "/api/memberapi/workout/get":
            return copy.deepcopy(self.rows.get(params["id"]))
        if path == "/api/memberapi/trainingcard/get/valid":
            return [{"id": 20, "cardtype_name": "Two classes per week", "trainingsLeft": None}]
        if path.startswith("/api/memberapi/workoutBooking/"):
            number = params["workout"]
            if path.endswith("/remove"):
                self.own.pop(number, None)
            elif path.endswith("/join"):
                self.own[number] = {**self.rows[number], "booked": True, "inQueue": False}
            else:
                row = self.rows[number]
                full = row["space"] is not None and row["numBooked"] - row["numQueue"] >= row["space"]
                queue = full and not self.promote_during_join
                self.own[number] = {**row, "booked": True, "inQueue": queue, "queue": 1 if queue else None}
            if self.error_after_write:
                raise ZoeziError("network_error", "Response lost", exit_code=4)
            return {"id": 999, "inQueue": self.own.get(number, {}).get("inQueue", False)}
        if path == "/api/memberapi/logout":
            self.token = None
            return None
        return None


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.transport = FakeTransport()
        self.client = Client(root=self.root, tenant=TENANT, transport=self.transport, now=lambda: NOW)

    def tearDown(self):
        self.temp.cleanup()

    def posts(self):
        return [c for c in self.transport.calls if c[0] == "POST" and "workoutBooking" in c[1]]

    def test_book_and_cancel_preserve_other_booking(self):
        before = copy.deepcopy(self.transport.own[101])
        result = self.client.mutate_booking("create", 102)
        self.assertEqual(result["state"], "confirmed")
        self.assertEqual(self.client.mutate_booking("cancel", 102)["state"], "absent")
        self.assertEqual(self.transport.own[101], before)
        self.assertEqual([c[2]["workout"] for c in self.posts()], [102, 102])

    def test_every_dry_run_is_read_only(self):
        self.client.mutate_booking("create", 102, dry_run=True)
        self.client.mutate_booking("cancel", 101, dry_run=True)
        self.assertEqual(self.posts(), [])

    def test_booking_is_idempotent(self):
        self.assertFalse(self.client.mutate_booking("create", 101)["changed"])
        self.assertEqual(self.posts(), [])

    def test_cancelling_absent_is_idempotent(self):
        self.assertFalse(self.client.mutate_booking("cancel", 102)["changed"])
        self.assertEqual(self.posts(), [])

    def test_full_class_requires_explicit_waitlist(self):
        self.transport.rows[102].update(space=10, numBooked=12, numQueue=2)
        with self.assertRaisesRegex(ZoeziError, "full"):
            self.client.mutate_booking("create", 102)
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.client.mutate_booking("join", 102)["state"], "waitlisted")
        self.assertEqual(self.client.bookings()[1]["queue_position"], 1)
        self.assertEqual(self.client.mutate_booking("leave", 102)["state"], "absent")

    def test_claim_queue_uses_join_endpoint(self):
        self.transport.own[102] = {**workout(102, "04"), "booked": True, "inQueue": True}
        self.assertEqual(self.client.mutate_booking("claim", 102)["state"], "confirmed")
        self.assertTrue(self.posts()[0][1].endswith("/join"))

    def test_leave_queue_cannot_cancel_promoted_booking(self):
        with self.assertRaises(ZoeziError) as err:
            self.client.mutate_booking("leave", 101)
        self.assertEqual(err.exception.code, "confirmed_booking")
        self.assertEqual(self.posts(), [])

    def test_queue_join_race_reports_confirmed(self):
        self.transport.rows[102].update(space=2, numBooked=2, numQueue=0)
        self.transport.promote_during_join = True
        self.assertEqual(self.client.mutate_booking("join", 102)["state"], "confirmed")

    def test_leave_already_removed_queue_is_idempotent(self):
        self.assertEqual(self.client.mutate_booking("leave", 102)["state"], "absent")

    def test_late_cancel_requires_acknowledgement(self):
        self.client.now = lambda: datetime(2026, 11, 3, 16, tzinfo=self.client.tz)
        self.assertTrue(self.client.mutate_booking("cancel", 101, dry_run=True)["late_or_unknown_cancellation"])
        with self.assertRaises(ZoeziError) as err:
            self.client.mutate_booking("cancel", 101)
        self.assertEqual(err.exception.code, "late_cancellation")
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.client.mutate_booking("cancel", 101, allow_late=True)["state"], "absent")

    def test_unknown_cancellation_policy_is_not_assumed_free(self):
        self.transport.settings["workoutcategories"][0].pop("unregisterHoursBefore")
        with self.assertRaises(ZoeziError) as err:
            self.client.mutate_booking("cancel", 101)
        self.assertEqual(err.exception.code, "late_cancellation")

    def test_timeout_after_booking_is_reconciled_without_repeating_post(self):
        self.transport.error_after_write = True
        result = self.client.mutate_booking("create", 102)
        self.assertTrue(result["reconciled_after_transport_error"])
        self.assertEqual(len(self.posts()), 1)

    def test_cancel_readback_failure_is_not_success(self):
        original = self.transport.request

        def request(method, path, **kwargs):
            result = original(method, path, **kwargs)
            if path.endswith("workoutBooking/remove"):
                self.transport.failed_readback = True
            return result

        self.transport.request = request
        with self.assertRaises(ZoeziError) as err:
            self.client.mutate_booking("cancel", 101)
        self.assertEqual(err.exception.code, "mutation_unknown")
        self.assertEqual(len(self.posts()), 1)

    def test_future_window_and_timezone(self):
        rows = self.client.schedule(start="2026-11-03", days=1)
        self.assertEqual([r["workout_id"] for r in rows], [101])
        self.assertEqual(rows[0]["start"], "2026-11-03T17:00:00+01:00")
        self.assertTrue(rows[0]["booked"])

    def test_full_count_includes_queue_in_zoezi(self):
        row = self.client.normalize(workout(capacity=10, booked=12, queued=3))
        self.assertEqual(row["spots_available"], 1)

    def test_hidden_booking_count_is_unknown(self):
        row = workout()
        row.pop("numBooked")
        self.assertIsNone(self.client.normalize(row)["spots_available"])

    def test_family_bookings_are_not_mistaken_for_own(self):
        self.transport.own[102] = {**workout(102, "04"), "booked": True, "user_id": 999}
        self.assertEqual([r["workout_id"] for r in self.client.bookings()], [101])
        self.assertEqual(self.client.mutate_booking("create", 102)["state"], "confirmed")
        self.assertEqual(len(self.posts()), 1)

    def test_server_error_after_committed_write_is_reconciled(self):
        original = self.transport.request

        def request(method, path, **kwargs):
            result = original(method, path, **kwargs)
            if path.endswith("workoutBooking/add"):
                raise ZoeziError("upstream_rejected", "Internal error", http_status=500)
            return result

        self.transport.request = request
        self.assertTrue(self.client.mutate_booking("create", 102)["reconciled_after_transport_error"])
        self.assertEqual(len(self.posts()), 1)

    def test_booking_not_yet_open(self):
        self.transport.settings["workoutcategories"][0]["registerDaysBefore"] = 1
        with self.assertRaises(ZoeziError) as err:
            self.client.mutate_booking("create", 102)
        self.assertEqual(err.exception.code, "booking_not_open")

    def test_cancelled_workout_cannot_be_booked(self):
        self.transport.rows[102]["status"] = "Cancelled"
        with self.assertRaises(ZoeziError) as err:
            self.client.mutate_booking("create", 102)
        self.assertEqual(err.exception.code, "workout_cancelled")

    def test_ended_workout_cannot_be_booked(self):
        self.client.now = lambda: datetime(2026, 11, 5, 12, tzinfo=self.client.tz)
        with self.assertRaises(ZoeziError) as err:
            self.client.mutate_booking("create", 102)
        self.assertEqual(err.exception.code, "workout_ended")

    def test_setup_probes_before_persisting(self):
        result = self.client.setup("member@example.test")
        self.assertTrue(result["password_login_enabled"])
        self.assertEqual(read_json(self.root / "config.json")["tenant"], TENANT)

    def test_login_stores_session_but_never_password(self):
        self.client.login("member@example.test", "test-password")
        data = (self.root / "session.json").read_text()
        self.assertNotIn("test-password", data)
        if os.name != "nt":
            self.assertEqual((self.root / "session.json").stat().st_mode & 0o777, 0o600)

    def test_bankid_only_tenant_fails_without_attempting_bankid(self):
        self.transport.settings["login"]["mail"] = False
        with self.assertRaises(ZoeziError) as err:
            self.client.login("member@example.test", "test-password")
        self.assertEqual(err.exception.code, "password_login_disabled")
        self.assertFalse(any("bankid" in c[1] for c in self.transport.calls))

    def test_unattended_reauthentication_once(self):
        self.client.username = "member@example.test"
        self.transport.fail_auth_once = True
        with patch.dict(os.environ, {"ZOEZI_PASSWORD": "test-password"}):
            self.assertEqual(self.client.profile()["id"], 100)
        self.assertEqual(sum(c[1].endswith("/login") for c in self.transport.calls), 1)

    def test_403_does_not_retry_login(self):
        self.transport.forbidden = True
        with self.assertRaises(ZoeziError):
            self.client.profile()
        self.assertEqual(sum(c[1].endswith("/login") for c in self.transport.calls), 0)

    def test_no_session_without_credentials_fails_fast(self):
        self.transport.token = None
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(ZoeziError) as err:
            self.client.profile()
        self.assertEqual(err.exception.exit_code, 2)

    def test_no_cross_tenant_session_reuse(self):
        write_json(self.root / "session.json", {"tenant": "https://other.zoezi.se", "session": "other-session"})
        self.transport.token = None
        Client(root=self.root, tenant=TENANT, transport=self.transport)
        self.assertIsNone(self.transport.token)

    def test_logout_removes_local_state(self):
        self.client.login("member@example.test", "test-password")
        self.client.logout()
        self.assertFalse((self.root / "session.json").exists())
        self.assertIsNone(self.transport.token)

    def test_registration_dry_run_and_disabled(self):
        self.client.account_create({"mail": "member@example.test", "password": "fake"}, dry_run=True)
        self.assertFalse(any(c[1] == "/api/memberapi/add" for c in self.transport.calls))
        self.transport.settings["canCreate"] = False
        self.client._settings = None
        with self.assertRaises(ZoeziError):
            self.client.account_create({"mail": "member@example.test"})

    def test_profile_cannot_modify_another_member(self):
        with self.assertRaises(ZoeziError):
            self.client.profile_update({"id": 999, "firstname": "Other"})


class StorageTests(unittest.TestCase):
    def test_atomic_write_and_symlink_refusal(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            write_json(path, {"ok": True})
            self.assertEqual(read_json(path), {"ok": True})
            if os.name != "nt":
                link = Path(tmp) / "link.json"
                link.symlink_to(path)
                with self.assertRaises(ZoeziError):
                    write_json(link, {})

    def test_lock_excludes_other_invocations(self):
        with tempfile.TemporaryDirectory() as tmp, state_lock(Path(tmp)):
            with self.assertRaises(ZoeziError) as err:
                with state_lock(Path(tmp), timeout=0.01):
                    pass
            self.assertEqual(err.exception.code, "state_locked")

    def test_secret_redaction(self):
        result = redact({"session": "private", "message": "failed with test-password", "nested": {"password": "test-password"}}, ["test-password"])
        self.assertNotIn("test-password", json.dumps(result))
        self.assertNotIn("private", json.dumps(result))

    def test_tenant_urls(self):
        self.assertEqual(tenant_url("example.zoezi.se/mypages"), TENANT)
        for value in ["http://example.zoezi.se", "https://user:password@example.zoezi.se", "https://example.zoezi.se?token=secret"]:
            with self.assertRaises(ZoeziError):
                tenant_url(value)


if __name__ == "__main__":
    unittest.main()
