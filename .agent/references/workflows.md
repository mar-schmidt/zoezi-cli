# Detailed workflows

## Onboarding existing or new members

`setup` probes the facility before saving local configuration; paths such as `/mypages` normalize to the HTTPS origin. `doctor` reports password-login support. `profile` reports the current member and blocks; `memberships --all` includes historical cards. `account fields` discovers facility-specific registration/profile fields and terms.

Use `profile update --data-file profile.json --dry-run` before an explicitly requested change; only the current member can be edited. Use `account create --data-file member.json --dry-run` before explicitly requested self-registration. Removing `--dry-run` creates an account only when enabled by the facility. Treat those JSON files as secrets; never commit them. Account creation may require human acceptance of terms. Use `auth reset-password --username user@example.com --dry-run` to preview an explicitly requested reset email; removing `--dry-run` sends it. Neither action belongs in routine booking recovery.

The CLI does not purchase cards, pay invoices, accept contracts, open doors or manage other members. QR-entry check-in is available for the current member after an explicit on-site request. Use facility support for unresolved membership migration, unpaid dues or terms that prevent booking.

## QR entry contract

`checkin inspect --qr-stdin` resolves the code using `GET /api/door/scan?data=...`. Output only contains the resource's ID/name/type/active/punch metadata. `supported: true` identifies an active QR-entry resource; it is not a membership or attendance check. `checkin qr --qr-stdin --dry-run` additionally reads the current member and previews the operation. Without `--dry-run`, it sends one JSON body containing only `qrcode_entry_id` to `/api/memberapi/entry/qrcode`; it never supplies another member's ID.

Supply `ZOEZI_QR_CODE` securely when stdin is unavailable. Only `qrcode_entry` resources are supported; the app's separate `door/open` route, resource-booking service check-in, and admin attendance APIs are outside scope. A `punch` value of true or an unknown value requires `--allow-punch` after user acknowledgement. Inactive or unsupported resources are rejected before a write.

There is no documented member endpoint for reading back a specific QR-entry operation, nor an idempotency key. Check-in is **not idempotent**. Even an expired-authentication response after submission is never automatically retried. Recovery from a known failed session happens during the read preflight. Report `checked_in` only after an explicit boolean `success: true`, with `attendance_verified: false`; do not infer class attendance from a facility entry or from bookings. Report an ambiguous response, timeout or server error after submission as `checkin_unknown` and stop until the user checks the app or reception.

## Common errors

| Code | Meaning |
|---|---|
| `setup_required` | Configure tenant first. |
| `credentials_required`, `login_required` | No usable configured credentials/session. |
| `password_login_disabled` | Facility disabled the supported login method. |
| `permission_denied` | Account lacks access; 403 does not trigger a login retry. |
| `workout_full` | Use `waitlist join` if requested. |
| `workout_not_full` | Use regular booking for the available place. |
| `not_waitlisted` | No queue entry to claim. |
| `confirmed_booking` | Queue entry became a real reservation. |
| `booking_not_open`, `booking_closed` | Respect the returned local booking window. |
| `late_cancellation` | Deadline passed or unknown; review before `--allow-late`. |
| `mutation_unknown`, `mutation_unverified` | Read current reservation before another write. |
| `rate_limited` | Stop instead of polling or repeated retries. |
| `qr_code_required`, `invalid_qr_code` | Supply the scanned value securely; do not invent or expose it. |
| `qr_not_found`, `inactive_qr_resource` | Verify the facility and scan the current code. |
| `unsupported_qr_resource` | Use the facility app for a door or other unsupported resource. |
| `punch_acknowledgement_required` | Explain possible punch consumption before `--allow-punch`. |
| `checkin_rejected` | Surface Zoezi's reason, such as missing membership; do not bypass it. |
| `checkin_unknown` | Inspect the app/reception before another submission; booking readback cannot verify entry. |

Zoezi's confirmed occupancy is `numBooked - numQueue`. Unknown counts mean unknown availability. Card validity/remaining clips do not alone guarantee eligibility; Zoezi enforces all membership and weekly restrictions when booking.

## Calendar objects

`calendar events` returns `source_key`, `uid`, `workout_id`, `summary`, `start`, `end`, `timezone`, `location`, `status` and `description`. Store `source_key` in the external event. It includes tenant and member ID, so family members booking the same workout remain separate.

Supply a JSON array with those fields plus actual `event_id` values to `calendar plan`. Its `changes` contain `create`, `update` and `delete`. It excludes unrelated events, other members and dates outside the requested window. A plan is not proof of external changes. Use the actual connected calendar provider to apply and verify it.

Treat subscription URLs as credentials. ICS snapshots use stable UIDs, UTC timestamps and RFC 5545 escaping/folding. Subscription refresh timing belongs to the calendar provider; use direct connector writes for timely family visibility.
