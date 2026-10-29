from __future__ import annotations

import re


class ZoeziError(Exception):
    def __init__(self, code: str, message: str, *, exit_code: int = 3, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code
        self.details = details

    def as_dict(self):
        return {"ok": False, "error": self.message, "code": self.code, "details": self.details}


PRIVATE_KEYS = re.compile(
    r"^(?:password|newPassword|secret|token|(?:access|refresh|id)[_-]?token|api[_-]?key|(?:_.*)?session|icalendarid|login_key|personalCodeNumber)$",
    re.I,
)


def redact(value, secrets=()):
    if isinstance(value, dict):
        return {k: "[redacted]" if PRIVATE_KEYS.search(k) else redact(v, secrets) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v, secrets) for v in value]
    if isinstance(value, str):
        for secret in secrets:
            if secret:
                value = value.replace(str(secret), "[redacted]")
    return value
