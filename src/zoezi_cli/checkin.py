"""The member app's QR-entry flow, with read-only scanning and one write at most."""
from __future__ import annotations

from .client import local_time
from .errors import ZoeziError


def inspect_qr(client, code):
    if isinstance(code, str):
        client.secrets.append(code)
        code = code.strip()
        client.secrets.append(code)
    if not isinstance(code, str) or not code or len(code) > 4096 or any(c.isspace() or not c.isprintable() for c in code):
        raise ZoeziError("invalid_qr_code", "Supply the scanned QR value through stdin or ZOEZI_QR_CODE.", exit_code=1)
    # This is the same read-only lookup the official app makes before dispatching
    # to either QR entry or door opening. Never follow URLs embedded in a QR code.
    raw = client.transport.request("GET", "/api/door/scan", params={"data": code})
    if not raw:
        raise ZoeziError("qr_not_found", "Zoezi did not recognize this QR code at the configured facility.", exit_code=1)
    if (not isinstance(raw, dict) or type(raw.get("id")) is not int or raw["id"] <= 0
            or not isinstance(raw.get("resourceType"), str)
            or (raw.get("name") is not None and not isinstance(raw["name"], str))
            or (raw.get("active") is not None and type(raw["active"]) is not bool)
            or (raw.get("punch") is not None and type(raw["punch"]) is not bool)):
        raise ZoeziError("invalid_response", "Zoezi returned an invalid QR resource.")
    resource = {"id": raw["id"], "name": raw.get("name"), "type": raw["resourceType"],
                "active": raw.get("active"), "punch": raw.get("punch")}
    return {"tenant": client.tenant, "resource": resource,
            "supported": resource["type"] == "qrcode_entry" and resource["active"] is True,
            "may_consume_punch": resource["punch"] is not False,
            "note": "A scan only identifies the resource; it does not register entry or validate membership."}


def submit_qr(client, code, *, dry_run=False, allow_punch=False):
    info = inspect_qr(client, code)
    resource = info["resource"]
    if resource["type"] != "qrcode_entry":
        raise ZoeziError("unsupported_qr_resource", "This QR resource is not QR-entry check-in. Use the facility app for door access.",
                         exit_code=1, resource=resource)
    if resource["active"] is not True:
        raise ZoeziError("inactive_qr_resource", "This QR-entry resource is inactive or its active status is unknown.", exit_code=1, resource=resource)
    member = client.profile()
    plan = {"operation": "qr_checkin", "tenant": client.tenant, "member_id": member["id"], "resource": resource,
            "may_consume_punch": info["may_consume_punch"],
            "requires_punch_acknowledgement": info["may_consume_punch"] and not allow_punch,
            "attendance_verified": False}
    if dry_run:
        return {"dry_run": True, "state": "not_submitted", **plan}
    if plan["requires_punch_acknowledgement"]:
        raise ZoeziError("punch_acknowledgement_required", "Check-in may deduct a training-card punch; review the preview before --allow-punch.",
                         exit_code=1, resource=resource)
    try:
        # Authenticate during the read preflight. Do NOT use Client.request here:
        # even an auth retry must never resubmit an entry that can consume a punch.
        response = client.transport.request("POST", "/api/memberapi/entry/qrcode",
                                            body={"qrcode_entry_id": code.strip()})
    except ZoeziError as exc:
        if exc.code in ("network_error", "invalid_response") or exc.details.get("http_status", 0) >= 500:
            raise unknown_result(resource) from exc
        raise
    if not isinstance(response, dict) or type(response.get("success")) is not bool:
        raise unknown_result(resource)
    if response["success"] is False:
        raise ZoeziError("checkin_rejected", "Zoezi rejected the QR check-in.", resource=resource,
                         reason=text_field(response, "reason"), entry_message=text_field(response, "entryMessage"),
                         missing_membership=response.get("missingMembership") is True,
                         card_not_valid_for_door=response.get("cardNotValidForDoor") is True)
    if response.get("user_id") not in (None, member["id"]) or response.get("door") not in (None, resource["id"]):
        raise unknown_result(resource)
    try:
        entry_time = local_time(text_field(response, "entryTime"), client.tz)
    except ValueError:
        entry_time = None
    return {"dry_run": False, "state": "checked_in", "verified_by": "zoezi_response", **plan,
            "entry_time": entry_time.isoformat() if entry_time else None, "entry_message": text_field(response, "entryMessage")}


def text_field(response, key):
    return response.get(key) if isinstance(response.get(key), str) else None


def unknown_result(resource):
    return ZoeziError("checkin_unknown", "Check-in may have registered. Check the app or reception before another attempt; do not repeat automatically.",
                      exit_code=4, resource=resource, attendance_verified=False)
