from __future__ import annotations

import http.cookiejar
import json
import socket
import urllib.error
import urllib.parse
import urllib.request

from .errors import ZoeziError


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (new.scheme, new.netloc) != (old.scheme, old.netloc):
            raise ZoeziError("unsafe_redirect", "Refusing to forward a request to another origin.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Transport:
    def __init__(self, tenant: str, *, timeout=30, api_key=None):
        self.tenant = tenant
        self.timeout = timeout
        self.api_key = api_key
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies), SameOriginRedirect()
        )

    def set_session(self, session: str | None):
        self.cookies.clear()
        if session:
            host = urllib.parse.urlsplit(self.tenant).hostname
            self.cookies.set_cookie(http.cookiejar.Cookie(
                version=0, name="session", value=session, port=None, port_specified=False,
                domain=host, domain_specified=False, domain_initial_dot=False,
                path="/", path_specified=True, secure=True, expires=None, discard=True,
                comment=None, comment_url=None, rest={}, rfc2109=False,
            ))

    def session(self):
        return next((cookie.value for cookie in self.cookies if cookie.name == "session"), None)

    def request(self, method: str, path: str, *, params=None, body=None, text=False):
        url = self.tenant + path
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        headers = {"Accept": "text/calendar" if text else "application/json", "User-Agent": "zoezi-cli/0.1"}
        if self.api_key:
            headers["Authorization"] = "Zoezi " + self.api_key
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                if text:
                    return raw
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except ValueError as exc:
                    raise ZoeziError("invalid_response", "API returned a non-JSON response.", endpoint=path) from exc
        except urllib.error.HTTPError as exc:
            message = "Zoezi rejected the request."
            kind = None
            try:
                payload = json.loads(exc.read())
                message = payload.get("message") or message
                kind = payload.get("type")
            except (ValueError, AttributeError):
                pass
            if exc.code in (401, 403):
                raise ZoeziError("auth_rejected" if exc.code == 401 else "permission_denied", message,
                                 exit_code=2, http_status=exc.code, upstream_type=kind) from exc
            raise ZoeziError("rate_limited" if exc.code == 429 else "upstream_rejected", message,
                             http_status=exc.code, upstream_type=kind, endpoint=path) from exc
        except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
            raise ZoeziError("network_error", "Cannot reach Zoezi; the result of a write may be unknown.",
                             exit_code=4, endpoint=path) from exc
