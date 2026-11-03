import contextlib
import copy
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from zoezi_cli import checkin
from zoezi_cli.cli import main, parser, run
from zoezi_cli.client import Client
from zoezi_cli.errors import ZoeziError, redact
from zoezi_cli.transport import Transport

from test_client import FakeTransport, NOW, TENANT


QR = "synthetic-qr-value"


class CheckinTransport(FakeTransport):
    def __init__(self):
        super().__init__()
        self.resource = {"id": 14, "name": "Class check-in", "resourceType": "qrcode_entry",
                         "active": True, "punch": False, "qrcode_entry_id": QR}
        self.entry_response = {"success": True, "user_id": 100, "door": 14, "entryTime": "2026-11-03 16:50:00"}
        self.entry_error = None
        self.scan_error = None
        self.entries = 0

    def request(self, method, path, *, params=None, body=None, **kwargs):
        if path == "/api/door/scan":
            self.calls.append((method, path, params, body))
            if self.scan_error:
                raise self.scan_error
            return copy.deepcopy(self.resource)
        if path == "/api/memberapi/entry/qrcode":
            self.calls.append((method, path, params, body))
            self.entries += 1
            if self.entry_error:
                raise self.entry_error
            return copy.deepcopy(self.entry_response)
        return super().request(method, path, params=params, body=body, **kwargs)


class CheckinTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.transport = CheckinTransport()
        self.client = Client(root=Path(self.temp.name), tenant=TENANT, transport=self.transport, now=lambda: NOW)

    def tearDown(self):
        self.temp.cleanup()

    def posts(self):
        return [c for c in self.transport.calls if c[0] == "POST"]

    def test_inspect_is_anonymous_read_only_and_does_not_expose_qr(self):
        self.transport.token = None
        with patch.dict(os.environ, {}, clear=True):
            result = checkin.inspect_qr(self.client, QR)
        self.assertEqual(result["resource"]["id"], 14)
        self.assertTrue(result["supported"])
        self.assertNotIn(QR, json.dumps(result))
        self.assertEqual(self.transport.calls, [("GET", "/api/door/scan", {"data": QR}, None)])

    def test_dry_run_authenticates_without_entry_or_booking_changes(self):
        before = copy.deepcopy(self.transport.own)
        result = checkin.submit_qr(self.client, QR, dry_run=True)
        self.assertEqual(result["state"], "not_submitted")
        self.assertFalse(result["attendance_verified"])
        self.assertEqual(result["member_id"], 100)
        self.assertEqual(self.posts(), [])
        self.assertEqual(self.transport.own, before)

    def test_submit_uses_only_current_member_and_reports_explicit_ack(self):
        before = copy.deepcopy(self.transport.own)
        self.transport.entry_response["Member"] = {"mail": "private@example.test", "password": "private-password"}
        result = checkin.submit_qr(self.client, QR)
        self.assertEqual(self.posts(), [("POST", "/api/memberapi/entry/qrcode", None, {"qrcode_entry_id": QR})])
        self.assertEqual(result["state"], "checked_in")
        self.assertEqual(result["verified_by"], "zoezi_response")
        self.assertEqual(result["entry_time"], "2026-11-03T16:50:00+01:00")
        self.assertFalse(result["attendance_verified"])
        self.assertNotIn("private", json.dumps(result))
        self.assertEqual(self.transport.own, before)
        self.assertFalse(any(QR in p.read_text() for p in Path(self.temp.name).rglob("*.json")))

    def test_invalid_optional_entry_time_does_not_hide_explicit_success(self):
        self.transport.entry_response["entryTime"] = "unrecognized-timestamp"
        result = checkin.submit_qr(self.client, QR)
        self.assertEqual(result["state"], "checked_in")
        self.assertIsNone(result["entry_time"])

    def test_unrecognized_inactive_and_door_codes_never_post(self):
        for value, code in [(None, "qr_not_found"),
                            ({**self.transport.resource, "active": False}, "inactive_qr_resource"),
                            ({**self.transport.resource, "active": None}, "inactive_qr_resource"),
                            ({**self.transport.resource, "resourceType": "door"}, "unsupported_qr_resource"),
                            ({**self.transport.resource, "resourceType": "unknown"}, "unsupported_qr_resource")]:
            with self.subTest(code=code):
                self.transport.resource = value
                with self.assertRaises(ZoeziError) as err:
                    checkin.submit_qr(self.client, QR)
                self.assertEqual(err.exception.code, code)
                self.assertEqual(self.posts(), [])

    def test_punch_or_unknown_cost_requires_explicit_acknowledgement(self):
        for punch in (True, None):
            with self.subTest(punch=punch):
                self.transport.resource["punch"] = punch
                preview = checkin.submit_qr(self.client, QR, dry_run=True)
                self.assertTrue(preview["requires_punch_acknowledgement"])
                with self.assertRaises(ZoeziError) as err:
                    checkin.submit_qr(self.client, QR)
                self.assertEqual(err.exception.code, "punch_acknowledgement_required")
                self.assertEqual(self.posts(), [])
        checkin.submit_qr(self.client, QR, allow_punch=True)
        self.assertEqual(len(self.posts()), 1)

    def test_membership_rejection_preserves_reason_and_never_retries(self):
        self.transport.entry_response = {"success": False, "reason": "noValidCard", "missingMembership": True,
                                         "Member": {"mail": "private@example.test"}}
        with self.assertRaises(ZoeziError) as err:
            checkin.submit_qr(self.client, QR)
        self.assertEqual(err.exception.code, "checkin_rejected")
        self.assertEqual(err.exception.details["reason"], "noValidCard")
        self.assertNotIn("private", json.dumps(err.exception.as_dict()))
        self.assertEqual(len(self.posts()), 1)

    def test_lost_response_or_server_error_does_not_resubmit(self):
        for error in [ZoeziError("network_error", "Response lost", exit_code=4),
                      ZoeziError("upstream_rejected", "Failure", http_status=503),
                      ZoeziError("invalid_response", "Not JSON")]:
            with self.subTest(error=error.code):
                self.transport.calls.clear()
                self.transport.entry_error = error
                with self.assertRaises(ZoeziError) as err:
                    checkin.submit_qr(self.client, QR)
                self.assertEqual(err.exception.code, "checkin_unknown")
                self.assertEqual(err.exception.exit_code, 4)
                self.assertEqual(len(self.posts()), 1)

    def test_even_auth_rejection_after_submit_is_not_retried(self):
        self.client.username = "member@example.test"
        self.transport.entry_error = ZoeziError("auth_rejected", "Expired", exit_code=2)
        with patch.dict(os.environ, {"ZOEZI_PASSWORD": "synthetic-password"}), self.assertRaises(ZoeziError) as err:
            checkin.submit_qr(self.client, QR)
        self.assertEqual(err.exception.code, "auth_rejected")
        self.assertEqual(len(self.posts()), 1)

    def test_preflight_can_recover_session_before_one_entry_post(self):
        self.client.username = "member@example.test"
        self.transport.fail_auth_once = True
        with patch.dict(os.environ, {"ZOEZI_PASSWORD": "test-password"}):
            checkin.submit_qr(self.client, QR)
        self.assertEqual(sum(c[1].endswith("/login") for c in self.posts()), 1)
        self.assertEqual(sum(c[1] == "/api/memberapi/entry/qrcode" for c in self.posts()), 1)

    def test_ambiguous_acknowledgements_do_not_claim_success(self):
        for response in [None, True, {}, {"success": "true"}, {"success": 1}, {"success": True, "user_id": 999},
                         {"success": True, "door": 99}]:
            with self.subTest(response=response):
                self.transport.calls.clear()
                self.transport.entry_response = response
                with self.assertRaises(ZoeziError) as err:
                    checkin.submit_qr(self.client, QR)
                self.assertEqual(err.exception.code, "checkin_unknown")
                self.assertEqual(len(self.posts()), 1)

    def test_input_and_scan_failures_never_post(self):
        for value in [None, "", "\n", "x y", "x\x00y", "x" * 4097]:
            with self.subTest(value=repr(value)[:30]), self.assertRaises(ZoeziError):
                checkin.submit_qr(self.client, value)
        self.transport.scan_error = ZoeziError("upstream_rejected", "Unavailable", http_status=503)
        with self.assertRaises(ZoeziError):
            checkin.submit_qr(self.client, QR)
        self.assertEqual(self.posts(), [])

    def test_qr_input_sources_and_output_redaction(self):
        for argv, env, stdin in [(["checkin", "inspect", "--qr-stdin"], {}, QR + "\n"),
                                 (["checkin", "qr", "--dry-run"], {"ZOEZI_QR_CODE": QR}, "")]:
            with self.subTest(argv=argv), patch.dict(os.environ, env, clear=True), patch("sys.stdin", io.StringIO(stdin)):
                output = run(parser().parse_args(argv), self.client)
                self.assertNotIn(QR, json.dumps(self.client.safe(output)))
        self.transport.scan_error = ZoeziError("upstream_rejected", "Rejected " + QR)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("zoezi_cli.cli.Client", return_value=self.client), patch("sys.argv", ["zoezi", "checkin", "qr", "--qr-stdin"]), \
                patch("sys.stdin", io.StringIO(QR + "\n")), contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            with self.assertRaises(SystemExit):
                main()
        self.assertNotIn(QR, stderr.getvalue())
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(json.loads(stderr.getvalue())["code"], "upstream_rejected")
        self.assertNotIn(QR, json.dumps(redact({"qrcode_entry_id": QR, "qr_code": QR})))

    def test_missing_qr_is_noninteractive_error(self):
        with patch.dict(os.environ, {}, clear=True), patch("sys.stdin", io.StringIO("")), self.assertRaises(ZoeziError) as err:
            run(parser().parse_args(["checkin", "qr"]), self.client)
        self.assertEqual(err.exception.code, "qr_code_required")
        self.assertEqual(self.transport.calls, [])

    def test_checkin_setting_is_not_a_gate_for_qr_entry(self):
        self.transport.settings.update(checkin=False, showqr=True)
        self.assertTrue(self.client.doctor()["qr_scanner_enabled"])
        self.assertFalse(self.client.doctor()["checkin_module_enabled"])
        self.assertEqual(checkin.submit_qr(self.client, QR)["state"], "checked_in")


class CheckinWireTests(unittest.TestCase):
    def test_write_sends_qr_in_json_body_not_url(self):
        transport = Transport(TENANT)
        transport.opener = Mock()
        response = Mock()
        response.read.return_value = b'{"success":true}'
        transport.opener.open.return_value.__enter__ = Mock(return_value=response)
        transport.opener.open.return_value.__exit__ = Mock(return_value=False)
        transport.request("POST", "/api/memberapi/entry/qrcode", body={"qrcode_entry_id": QR})
        request = transport.opener.open.call_args[0][0]
        self.assertNotIn(QR, request.full_url)
        self.assertEqual(json.loads(request.data), {"qrcode_entry_id": QR})


if __name__ == "__main__":
    unittest.main()
