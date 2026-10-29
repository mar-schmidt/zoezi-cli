import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
import urllib.error
import urllib.request
from unittest.mock import Mock
import zipfile

from zoezi_cli.errors import ZoeziError, redact
from zoezi_cli.transport import SameOriginRedirect, Transport

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from skill_package import build, command_manifest, validate  # noqa: E402


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.transport = Transport("https://example.zoezi.se")
        self.transport.opener = Mock()

    def test_login_is_json_body_and_password_never_url(self):
        response = Mock()
        response.read.return_value = b'{"id":100}'
        self.transport.opener.open.return_value.__enter__ = Mock(return_value=response)
        self.transport.opener.open.return_value.__exit__ = Mock(return_value=False)
        self.transport.request("POST", "/api/v8.0/memberapi/login", body={"login": "user@example.test", "password": "fake-secret"})
        request = self.transport.opener.open.call_args[0][0]
        self.assertNotIn("fake-secret", request.full_url)
        self.assertEqual(json.loads(request.data)["password"], "fake-secret")

    def test_session_cookie_is_secure_and_origin_bound(self):
        self.transport.set_session("synthetic-token")
        cookie = next(iter(self.transport.cookies))
        self.assertTrue(cookie.secure)
        self.assertEqual(cookie.domain, "example.zoezi.se")
        self.assertEqual(self.transport.session(), "synthetic-token")
        self.transport.set_session(None)
        self.assertIsNone(self.transport.session())

    def test_http_errors_do_not_include_raw_response(self):
        for status, code in [(401, "auth_rejected"), (403, "permission_denied"), (429, "rate_limited"), (500, "upstream_rejected")]:
            error = urllib.error.HTTPError("https://example.zoezi.se", status, "Error", {}, io.BytesIO(b'{"message":"Rejected","password":"sensitive-raw-field"}'))
            self.transport.opener.open.side_effect = error
            with self.assertRaises(ZoeziError) as raised:
                self.transport.request("GET", "/api/memberapi/get/current")
            self.assertEqual(raised.exception.code, code)
            self.assertNotIn("sensitive-raw-field", json.dumps(raised.exception.as_dict()))

    def test_network_errors_are_stable(self):
        self.transport.opener.open.side_effect = urllib.error.URLError("DNS details")
        with self.assertRaises(ZoeziError) as err:
            self.transport.request("GET", "/api/memberapi/get/current")
        self.assertEqual(err.exception.code, "network_error")
        self.assertEqual(err.exception.exit_code, 4)

    def test_redirects_cannot_forward_to_another_origin(self):
        request = urllib.request.Request("https://example.zoezi.se/login", data=b"secret")
        with self.assertRaises(ZoeziError):
            SameOriginRedirect().redirect_request(request, None, 302, "Found", {}, "https://other.example/login")

    def test_capability_flags_are_not_redacted(self):
        result = redact({"password_login_enabled": True, "password": "private", "access_token": "private"})
        self.assertTrue(result["password_login_enabled"])
        self.assertEqual(result["access_token"], "[redacted]")


class PackageTests(unittest.TestCase):
    def test_skill_examples_parse_and_generated_manifest_covers_mutations(self):
        validate()
        names = {row["command"] for row in command_manifest()["commands"]}
        self.assertTrue({"zoezi bookings create", "zoezi bookings cancel", "zoezi waitlist join", "zoezi waitlist claim", "zoezi waitlist leave", "zoezi auth login", "zoezi calendar plan"} <= names)

    def test_skill_archives_are_deterministic_and_versioned(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            build("0.1.0.post7", "a" * 40, target)
            first = hashlib.sha256((target / "zoezi-cli.skill").read_bytes()).hexdigest()
            build("0.1.0.post7", "a" * 40, target)
            self.assertEqual(first, hashlib.sha256((target / "zoezi-cli.skill").read_bytes()).hexdigest())
            with zipfile.ZipFile(target / "zoezi-cli.skill") as archive:
                manifest = json.loads(archive.read("zoezi-cli/skill.json"))
                self.assertEqual(manifest["version"], "0.1.0.post7")
                self.assertEqual(manifest["commit"], "a" * 40)
                self.assertTrue(all(not name.startswith("/") and ".." not in name for name in archive.namelist()))
                self.assertIn("zoezi-cli/agents/openai.yaml", archive.namelist())


if __name__ == "__main__":
    unittest.main()
