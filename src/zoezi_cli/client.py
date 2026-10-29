from __future__ import annotations

import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .errors import ZoeziError, redact
from .storage import CredentialStore, read_json, state_root, write_json
from .transport import Transport


def tenant_url(value: str) -> str:
    parsed = urlsplit(value if "://" in value else "https://" + value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.port not in (None, 443)):
        raise ZoeziError("invalid_tenant", "Use an HTTPS Zoezi tenant URL without credentials or query parameters.", exit_code=1)
    return "https://" + parsed.hostname.lower()


def timezone(name: str):
    try:
        return ZoneInfo(name)
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ZoeziError("invalid_timezone", "Unknown IANA time zone.", exit_code=1, timezone=name) from exc


def local_time(value, tz):
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)


def as_id(value):
    try:
        result = int(value)
        if result <= 0:
            raise ValueError
        return result
    except (ValueError, TypeError) as exc:
        raise ZoeziError("invalid_workout_id", "Workout IDs must be positive integers read from Zoezi.", exit_code=1) from exc


class Client:
    def __init__(self, *, root: Path | None = None, tenant=None, tz=None, transport=None, now=None, auto_login=True):
        self.root = root or state_root()
        self.config = read_json(self.root / "config.json", {})
        selected = tenant or os.environ.get("ZOEZI_URL") or self.config.get("tenant")
        if not selected:
            raise ZoeziError("setup_required", "Run zoezi setup --url https://your-box.zoezi.se first.", exit_code=1)
        self.tenant = tenant_url(selected)
        self.tz = timezone(tz or os.environ.get("ZOEZI_TIMEZONE") or self.config.get("timezone", "Europe/Stockholm"))
        self.now = now or (lambda: datetime.now(self.tz))
        self.transport = transport or Transport(self.tenant, api_key=os.environ.get("ZOEZI_API_KEY"))
        self.auto_login = auto_login
        self.username = os.environ.get("ZOEZI_USERNAME") or self.config.get("username")
        self.secrets = [os.environ.get("ZOEZI_PASSWORD"), os.environ.get("ZOEZI_API_KEY")]
        self._settings = None
        state = read_json(self.root / "session.json", {})
        if state.get("tenant") == self.tenant and (not self.username or state.get("username") == self.username):
            self.transport.set_session(state.get("session"))
            self.username = self.username or state.get("username")
            self.secrets.append(state.get("session"))

    def settings(self):
        if self._settings is None:
            self._settings = self.transport.request("GET", "/api/public/settings/get")
        return self._settings

    def setup(self, username=None, calendar_id=None):
        settings = self.settings()
        config = {"tenant": self.tenant, "timezone": str(self.tz)}
        if username or self.username:
            config["username"] = username or self.username
        if calendar_id:
            config["calendar_id"] = calendar_id
        elif self.config.get("tenant") == self.tenant and self.config.get("calendar_id"):
            config["calendar_id"] = self.config["calendar_id"]
        write_json(self.root / "config.json", config)
        self.config = config
        self.username = config.get("username")
        return {"config": config, "facility": settings.get("name"),
                "password_login_enabled": bool((settings.get("login") or {}).get("mail"))}

    def login(self, username, password, *, save_credentials=False):
        if not username or not password:
            raise ZoeziError("credentials_required", "Supply username and password via prompt, stdin or environment.", exit_code=2)
        if not (self.settings().get("login") or {}).get("mail"):
            raise ZoeziError("password_login_disabled", "This tenant has disabled password login. BankID is not supported.", exit_code=2)
        self.transport.set_session(None)
        self.secrets.append(password)
        member = self.transport.request("POST", "/api/v8.0/memberapi/login",
                                        body={"login": username, "password": password, "no_cookie": False})
        if not isinstance(member, dict) or not member.get("id"):
            raise ZoeziError("auth_rejected", "Zoezi did not return an authenticated member.", exit_code=2)
        session = self.transport.session() or member.get("session") or member.get("__session")
        if not session:
            raise ZoeziError("session_missing", "Login did not return a usable member session.", exit_code=2)
        self.transport.set_session(session)
        self.secrets.append(session)
        self.username = username
        write_json(self.root / "session.json", {"tenant": self.tenant, "username": username, "session": session})
        self.config.update(tenant=self.tenant, timezone=str(self.tz), username=username)
        write_json(self.root / "config.json", self.config)
        if save_credentials:
            CredentialStore(self.tenant, username).set(password)
        return {"authenticated": True, "member": self.member_summary(member), "credentials_saved": save_credentials}

    def reauthenticate(self):
        password = os.environ.get("ZOEZI_PASSWORD")
        if not password and self.username:
            password = CredentialStore(self.tenant, self.username).get()
        if self.auto_login and self.username and password:
            self.login(self.username, password)
            return
        raise ZoeziError("login_required", "Run zoezi auth login, or supply unattended credentials securely.", exit_code=2)

    def request(self, method, path, *, params=None, body=None):
        if not self.transport.session():
            self.reauthenticate()
        try:
            return self.transport.request(method, path, params=params, body=body)
        except ZoeziError as exc:
            # Retry only a definitive 401 (authentication failed before the operation).
            # Never retry a write after timeout, 403, 429 or 5xx.
            if exc.code != "auth_rejected" or not self.auto_login:
                raise
            self.reauthenticate()
            return self.transport.request(method, path, params=params, body=body)

    def logout(self, *, forget_credentials=False):
        error = None
        try:
            if self.transport.session():
                self.transport.request("POST", "/api/memberapi/logout")
        except ZoeziError as exc:
            error = exc
        finally:
            self.transport.set_session(None)
            state = read_json(self.root / "session.json", {})
            if state.get("tenant") == self.tenant:
                (self.root / "session.json").unlink(missing_ok=True)
            if forget_credentials and self.username:
                CredentialStore(self.tenant, self.username).delete()
        if error:
            raise ZoeziError("remote_logout_failed", "Local session cleared; remote logout could not be verified.", exit_code=4)
        return {"authenticated": False, "credentials_forgotten": forget_credentials}

    @staticmethod
    def member_summary(member):
        summary = {k: member.get(k) for k in ["id", "firstname", "lastname", "mail", "language", "site", "blocks", "recentWarnings"]}
        summary["pending_terms"] = member.get("acceptterms")
        return summary

    def profile(self):
        return self.member_summary(self.request("GET", "/api/memberapi/get/current"))

    def fields(self):
        settings = self.settings()
        return {"registration_enabled": settings.get("canCreate", False), "fields": settings.get("fields", []),
                "terms_url": settings.get("terms"), "pending_terms": settings.get("acceptterms")}

    def account_create(self, data, *, dry_run=False):
        if not self.settings().get("canCreate"):
            raise ZoeziError("registration_disabled", "The facility does not allow member self-registration.", exit_code=1)
        if not isinstance(data, dict):
            raise ZoeziError("invalid_data", "Member data must be a JSON object.", exit_code=1)
        forbidden = {"permissions", "isAdmin", "money", "session", "__session", "id"} & data.keys()
        if forbidden:
            raise ZoeziError("invalid_data", "Member data contains reserved fields.", exit_code=1)
        if dry_run:
            return {"dry_run": True, "operation": "account_create", "fields": sorted(data), "requirements": self.fields()}
        self.secrets.append(data.get("password"))
        member = self.transport.request("POST", "/api/memberapi/add", body=data)
        return {"member": self.member_summary(member), "next_step": "auth login"}

    def profile_update(self, data, *, dry_run=False):
        if not isinstance(data, dict):
            raise ZoeziError("invalid_data", "Profile data must be a JSON object.", exit_code=1)
        me = self.request("GET", "/api/memberapi/get/current")
        if "id" in data and data["id"] != me["id"]:
            raise ZoeziError("invalid_data", "Only the current member can be edited.", exit_code=1)
        if {"permissions", "isAdmin", "session", "__session", "money", "password"} & data.keys():
            raise ZoeziError("invalid_data", "Profile data contains reserved fields.", exit_code=1)
        body = {**data, "id": me["id"]}
        if dry_run:
            return {"dry_run": True, "operation": "profile_update", "changed_fields": sorted(data)}
        return {"member": self.member_summary(self.request("POST", "/api/memberapi/change", body=body))}

    def reset_password(self, username, *, dry_run=False):
        if not username:
            raise ZoeziError("credentials_required", "Supply the account email.", exit_code=1)
        if not dry_run:
            self.transport.request("POST", "/api/memberapi/requestResetPassword", params={"mail": username})
        return {"dry_run": dry_run, "reset_requested": not dry_run, "username": username}

    def memberships(self, *, all_cards=False):
        path = "/api/memberapi/trainingcard/get/" + ("all" if all_cards else "valid")
        cards = self.request("GET", path)
        return [{"id": card.get("id"), "name": card.get("cardtype_name") or card.get("name"),
                 "type_id": card.get("cardtype_id") or (card.get("card_type") or {}).get("id"),
                 "valid_from": card.get("validFromDate") or card.get("validFrom"),
                 "valid_to": card.get("validToDate") or card.get("validTo"),
                 "trainings_left": card.get("trainingsLeft"), "active": card.get("active"),
                 "type": card.get("type"), "freezes": card.get("freezes", [])} for card in cards]

    def window(self, start=None, days=7):
        if not 1 <= days <= 366:
            raise ZoeziError("invalid_window", "Use a date window of 1 to 366 days.", exit_code=1)
        try:
            first = date.fromisoformat(start) if start else self.now().date()
        except ValueError as exc:
            raise ZoeziError("invalid_date", "Use YYYY-MM-DD.", exit_code=1) from exc
        return first, first + timedelta(days=days)

    def normalize(self, workout):
        kind = workout.get("workoutType") or {}
        start, end = local_time(workout.get("startTime"), self.tz), local_time(workout.get("endTime"), self.tz)
        category = next((c for c in self.settings().get("workoutcategories", []) if c.get("id") == kind.get("category_id")), {})
        space = workout.get("space")
        booked_count = workout.get("numBooked")
        queue_count = workout.get("numQueue")
        remaining = None
        if space is not None and space != 0 and booked_count is not None and queue_count is not None:
            remaining = max(0, space - (booked_count - queue_count))
        return {
            "workout_id": workout.get("id"), "name": kind.get("name") or workout.get("name"),
            "extra_title": workout.get("extra_title"), "description": workout.get("description"),
            "start": start.isoformat() if start else None, "end": end.isoformat() if end else None,
            "timezone": str(self.tz), "site_id": workout.get("site_id"), "category_id": kind.get("category_id"),
            "status": workout.get("status"), "cancelled": workout.get("status") == "Cancelled",
            "booked": bool(workout.get("booked")) and not bool(workout.get("inQueue")),
            "in_queue": bool(workout.get("inQueue")), "queue_position": workout.get("queue"),
            "capacity": space, "spots_available": remaining, "queue_count": queue_count,
            "bookable_for_member": workout.get("bookableForCustomer"),
            "locations": [r.get("name") for r in workout.get("resources", []) if r.get("name")],
            "instructors": [r.get("name") for r in workout.get("staffs", []) if r.get("name")],
            "booking_opens": self.booking_opens(start, category),
            "booking_closes": (end if category.get("allowBookingAfterStarted") else start - timedelta(hours=category.get("registerHoursBefore") or 0)).isoformat() if start else None,
            "cancellation_deadline": (start - timedelta(hours=category["unregisterHoursBefore"])).isoformat() if start and category.get("unregisterHoursBefore") is not None else None,
            "queue_enabled": bool(self.settings().get("canQueue")), "first_come_queue": bool(category.get("firstCome")),
            "dropin_minutes": category.get("dropin") or 0,
        }

    def booking_opens(self, start, category):
        if not start or category.get("registerDaysBefore") is None:
            return None
        opens = start - timedelta(days=category["registerDaysBefore"])
        released = self.settings().get("wbreleasedat")
        if released:
            parts = str(released).split(":")
            opens = opens.replace(hour=int(parts[0]), minute=int(parts[1]), second=0)
        return opens.isoformat() if opens <= start else None

    def bookings(self, *, start=None, days=31):
        first, last = self.window(start, days)
        data = self.request("GET", "/api/memberapi/bookings/get", params={"startTime": str(first) + " 00:00:00", "endTime": str(last) + " 00:00:00"})
        # Zoezi may return inclusive boundaries. Apply an explicit half-open window.
        rows = [self.normalize(row) for row in (data or {}).get("workouts", [])]
        return sorted([row for row in rows if row["start"] and first <= local_time(row["start"], self.tz).date() < last], key=lambda row: row["start"])

    def schedule(self, *, start=None, days=7, name=None, site=None, bookable_only=False):
        first, last = self.window(start, days)
        data = self.transport.request("GET", "/api/public/workout/get/all", params={"fromDate": str(first), "toDate": str(last)})
        rows = [self.normalize(row) for row in (data or {}).get("workouts", [])]
        if self.transport.session() or (self.auto_login and self.username):
            try:
                own = {row["workout_id"]: row for row in self.bookings(start=str(first), days=days)}
                for row in rows:
                    if row["workout_id"] in own:
                        for key in ("booked", "in_queue", "queue_position"):
                            row[key] = own[row["workout_id"]][key]
            except ZoeziError as exc:
                if exc.code not in ("login_required", "auth_rejected"):
                    raise
        rows = [row for row in rows if row["start"] and first <= local_time(row["start"], self.tz).date() < last]
        if name:
            rows = [row for row in rows if name.casefold() in (str(row["name"] or "") + " " + str(row["extra_title"] or "")).casefold()]
        if site:
            rows = [row for row in rows if row["site_id"] == site]
        if bookable_only:
            now = self.now()
            rows = [row for row in rows if not row["cancelled"] and row["bookable_for_member"] is not False
                    and row["spots_available"] != 0 and (not row["booking_opens"] or local_time(row["booking_opens"], self.tz) <= now)
                    and (not row["booking_closes"] or now <= local_time(row["booking_closes"], self.tz))]
        return sorted(rows, key=lambda row: row["start"])

    def workout(self, workout_id):
        workout_id = as_id(workout_id)
        raw = self.request("GET", "/api/memberapi/workout/get", params={"id": workout_id})
        if not isinstance(raw, dict) or raw.get("id") != workout_id:
            raise ZoeziError("workout_not_found", "Zoezi did not return the requested workout.", exit_code=1)
        result = self.normalize(raw)
        return result

    def own_booking(self, workout):
        if not workout.get("start"):
            raise ZoeziError("invalid_response", "Workout has no start time.")
        day = local_time(workout["start"], self.tz).date().isoformat()
        return next((row for row in self.bookings(start=day, days=1) if row["workout_id"] == workout["workout_id"]), None)

    def mutate_booking(self, operation, workout_id, *, dry_run=False, allow_late=False):
        workout = self.workout(workout_id)
        own = self.own_booking(workout)
        confirmed = bool(own and own["booked"] and not own["cancelled"])
        queued = bool(own and own["in_queue"] and not own["cancelled"])
        cancel = operation in ("cancel", "leave")
        if operation == "leave" and confirmed:
            raise ZoeziError("confirmed_booking", "The queue entry is now a confirmed booking; use bookings cancel only if intended.", exit_code=1, workout=own)
        if (operation in ("create", "join") and confirmed) or (operation == "join" and queued):
            return {"changed": False, "dry_run": dry_run, "state": "confirmed" if confirmed else "waitlisted", "workout": own}
        if cancel and not confirmed and not queued:
            return {"changed": False, "dry_run": dry_run, "state": "absent", "workout": workout}
        if not cancel:
            if workout["cancelled"]:
                raise ZoeziError("workout_cancelled", "The workout is cancelled.", exit_code=1)
            if self.now() >= local_time(workout["end"] or workout["start"], self.tz):
                raise ZoeziError("workout_ended", "The workout has already ended.", exit_code=1)
            opens = local_time(workout["booking_opens"], self.tz)
            closes = local_time(workout["booking_closes"], self.tz)
            dropin = workout["dropin_minutes"]
            in_dropin = bool(dropin and local_time(workout["start"], self.tz) - timedelta(minutes=dropin) < self.now() < local_time(workout["start"], self.tz))
            if opens and self.now() < opens:
                raise ZoeziError("booking_not_open", "Booking has not opened yet.", exit_code=1, opens=workout["booking_opens"])
            if closes and self.now() > closes and not in_dropin:
                raise ZoeziError("booking_closed", "Booking has closed.", exit_code=1)
            if operation == "claim" and not queued:
                raise ZoeziError("not_waitlisted", "There is no active queue entry to claim.", exit_code=1)
            if operation == "create" and queued:
                return {"changed": False, "dry_run": dry_run, "state": "waitlisted", "workout": own,
                        "next_step": "waitlist claim if a first-come place is offered"}
            if operation == "create" and workout["spots_available"] == 0:
                raise ZoeziError("workout_full", "Workout is full; use waitlist join if desired.", exit_code=1)
            if operation == "join" and not workout["queue_enabled"]:
                raise ZoeziError("queue_disabled", "The facility has disabled workout queues.", exit_code=1)
            if operation == "join" and workout["spots_available"] != 0:
                raise ZoeziError("workout_not_full", "Use bookings create for an available place.", exit_code=1)
        deadline = local_time(workout["cancellation_deadline"], self.tz)
        late = bool(cancel and confirmed and (not deadline or self.now() > deadline))
        if late and not allow_late and not dry_run:
            raise ZoeziError("late_cancellation", "Cancellation deadline passed or policy is unknown; review dry-run before --allow-late.",
                             exit_code=1, workout=workout)
        plan = {"operation": operation, "workout": own or workout, "late_or_unknown_cancellation": late,
                "payment_method": None if cancel else "trainingcard"}
        if dry_run:
            return {"dry_run": True, "changed": False, **plan}
        path = "/api/memberapi/workoutBooking/" + ("remove" if cancel else "join" if operation == "claim" else "add")
        params = {"workout": workout["workout_id"]}
        if not cancel and operation != "claim":
            params["method"] = "trainingcard"
        uncertain = None
        try:
            response = self.request("POST", path, params=params)
            if isinstance(response, dict) and (response.get("success") is False or response.get("errorCode")):
                raise ZoeziError("booking_rejected", response.get("message") or "Zoezi rejected the operation.")
        except ZoeziError as exc:
            if exc.code not in ("network_error", "invalid_response") and exc.details.get("http_status", 0) < 500:
                raise
            uncertain = exc
        for attempt in range(2):
            read_ok = False
            try:
                actual = self.own_booking(workout)
                read_ok = True
            except ZoeziError:
                actual = None
                uncertain = uncertain or ZoeziError("readback_failed", "Cannot verify the booking outcome.")
            present = bool(actual and (actual["booked"] or actual["in_queue"]) and not actual["cancelled"])
            reached = (not present) if cancel else present and (operation != "claim" or actual["booked"])
            if reached and read_ok:
                return {"dry_run": False, "changed": True, "state": "absent" if cancel else "waitlisted" if actual["in_queue"] else "confirmed",
                        "reconciled_after_transport_error": uncertain is not None, "workout": actual or workout}
            if attempt == 0:
                time.sleep(0.25)
        raise ZoeziError("mutation_unknown" if uncertain else "mutation_unverified",
                         "Read bookings before doing anything else; do not repeat the mutation blindly.",
                         exit_code=4, workout_id=workout["workout_id"], operation=operation)

    def doctor(self):
        settings = self.settings()
        return {"tenant": self.tenant, "facility": settings.get("name"), "api_version": settings.get("version"),
                "timezone": str(self.tz), "password_login_enabled": bool((settings.get("login") or {}).get("mail")),
                "workout_booking_enabled": bool(settings.get("workoutbooking")), "queue_enabled": bool(settings.get("canQueue")),
                "registration_enabled": bool(settings.get("canCreate")), "calendar_id": self.config.get("calendar_id"),
                "unattended_credentials_configured": bool(os.environ.get("ZOEZI_PASSWORD") or (self.username and CredentialStore(self.tenant, self.username).get()))}

    def safe(self, value):
        return redact(value, self.secrets)
