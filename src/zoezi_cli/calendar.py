from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlsplit

from .client import local_time
from .errors import ZoeziError


def events(client, *, start=None, days=31, include_waitlist=False):
    member_id = client.profile()["id"]
    host = urlsplit(client.tenant).hostname
    result = []
    for workout in client.bookings(start=start, days=days):
        if not workout["booked"] and not (include_waitlist and workout["in_queue"]):
            continue
        key = f"zoezi:{host}:{member_id}:{workout['workout_id']}"
        result.append({
            "source_key": key, "uid": key + "@zoezi-cli", "workout_id": workout["workout_id"],
            "summary": ("[Waitlist] " if workout["in_queue"] else "") + (workout["extra_title"] or workout["name"] or "Training"),
            "start": workout["start"], "end": workout["end"], "timezone": str(client.tz),
            "location": ", ".join(workout["locations"]),
            "status": "CANCELLED" if workout["cancelled"] else "TENTATIVE" if workout["in_queue"] else "CONFIRMED",
            "description": "Managed by zoezi-cli. Source: " + key,
        })
    return result


def plan(client, existing, *, start=None, days=31, include_waitlist=False):
    """Plan provider-neutral changes; never write to a calendar implicitly."""
    if not isinstance(existing, list) or any(not isinstance(item, dict) for item in existing):
        raise ZoeziError("invalid_calendar_data", "Existing events must be a JSON array of event objects.", exit_code=1)
    desired = events(client, start=start, days=days, include_waitlist=include_waitlist)
    first, last = client.window(start, days)
    member_id = client.profile()["id"]
    prefix = f"zoezi:{urlsplit(client.tenant).hostname}:{member_id}:"
    current = {item["source_key"]: item for item in existing if str(item.get("source_key", "")).startswith(prefix)}
    changes = []
    wanted = {item["source_key"]: item for item in desired if item["status"] != "CANCELLED"}
    for key, item in wanted.items():
        old = current.get(key)
        if old is None:
            changes.append({"action": "create", "event": item})
        elif any(old.get(field) != item.get(field) for field in ("summary", "start", "end", "location", "status")):
            if not old.get("event_id"):
                raise ZoeziError("invalid_calendar_data", "Resolve the managed event's actual event_id before planning an update.", exit_code=1)
            changes.append({"action": "update", "event_id": old.get("event_id"), "event": item})
    for key, old in current.items():
        when = local_time(old.get("start"), client.tz)
        if key not in wanted and when and first <= when.date() < last:
            if not old.get("event_id"):
                raise ZoeziError("invalid_calendar_data", "Resolve the managed event's actual event_id before planning a deletion.", exit_code=1)
            changes.append({"action": "delete", "event_id": old.get("event_id"), "source_key": key})
    return {"calendar_id": client.config.get("calendar_id"), "changes": changes,
            "note": "Apply with your calendar connector, then read back. This command changes no calendar events."}


def escape(text):
    return str(text or "").replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "").replace(";", "\\;").replace(",", "\\,")


def fold(line):
    lines, current = [], ""
    for char in line:
        if len((current + char).encode()) > 75:
            lines.append(current)
            current = " " + char
        else:
            current += char
    return "\r\n".join(lines + [current])


def ics(event_list, now=None):
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//zoezi-cli//Personal bookings//EN", "CALSCALE:GREGORIAN"]
    for item in event_list:
        lines.extend(["BEGIN:VEVENT", "UID:" + escape(item["uid"]), "DTSTAMP:" + stamp,
                      "DTSTART:" + datetime.fromisoformat(item["start"]).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                      "DTEND:" + datetime.fromisoformat(item["end"]).astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                      "SUMMARY:" + escape(item["summary"]), "STATUS:" + item["status"],
                      "LOCATION:" + escape(item.get("location")), "DESCRIPTION:" + escape(item.get("description")), "END:VEVENT"])
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"
