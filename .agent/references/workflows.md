# Detailed workflows

## Onboarding existing or new members

`setup` probes the facility before saving local configuration; paths such as `/mypages` normalize to the HTTPS origin. `doctor` reports password-login support. `profile` reports the current member and blocks; `memberships --all` includes historical cards. `account fields` discovers facility-specific registration/profile fields and terms.

Use `profile update --data-file profile.json --dry-run` before an explicitly requested change; only the current member can be edited. Use `account create --data-file member.json --dry-run` before explicitly requested self-registration. Removing `--dry-run` creates an account only when enabled by the facility. Treat those JSON files as secrets; never commit them. Account creation may require human acceptance of terms. Use `auth reset-password --username user@example.com --dry-run` to preview an explicitly requested reset email; removing `--dry-run` sends it. Neither action belongs in routine booking recovery.

The CLI does not purchase cards, pay invoices, accept contracts, check in, open doors or manage other members. Use facility support for unresolved membership migration, unpaid dues or terms that prevent booking.

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

Zoezi's confirmed occupancy is `numBooked - numQueue`. Unknown counts mean unknown availability. Card validity/remaining clips do not alone guarantee eligibility; Zoezi enforces all membership and weekly restrictions when booking.

## Calendar objects

`calendar events` returns `source_key`, `uid`, `workout_id`, `summary`, `start`, `end`, `timezone`, `location`, `status` and `description`. Store `source_key` in the external event. It includes tenant and member ID, so family members booking the same workout remain separate.

Supply a JSON array with those fields plus actual `event_id` values to `calendar plan`. Its `changes` contain `create`, `update` and `delete`. It excludes unrelated events, other members and dates outside the requested window. A plan is not proof of external changes. Use the actual connected calendar provider to apply and verify it.

Treat subscription URLs as credentials. ICS snapshots use stable UIDs, UTC timestamps and RFC 5545 escaping/folding. Subscription refresh timing belongs to the calendar provider; use direct connector writes for timely family visibility.
