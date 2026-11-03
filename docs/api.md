# API coverage and observed behavior

Primary sources: [Zoezi API documentation](https://developer.zoezi.se/apidoc/), its [machine-readable schema](https://developer.zoezi.se/api/api/get), and the official member web client's request behavior. Inspection and live verification: 2026-10-29, API 11.4. Paths other than login use the facility's current API; login explicitly uses the documented v8.0 JSON contract.

| CLI capability | Method and path | Verification |
|---|---|---|
| Setup, fields, doctor, rules | GET `/api/public/settings/get` | Live |
| Password login / refresh | POST `/api/v8.0/memberapi/login` | Live login and recovery from an invalid session; also offline tested |
| Current member | GET `/api/memberapi/get/current` | Live |
| Schedule | GET `/api/public/workout/get/all` | Live |
| Workout details | GET `/api/memberapi/workout/get` | Live |
| Own bookings / queue status | GET `/api/memberapi/bookings/get` | Live |
| Book with training card | POST `/api/memberapi/workoutBooking/add?workout=...&method=trainingcard` | Live round trip |
| Cancel booking | POST `/api/memberapi/workoutBooking/remove?workout=...` | Live round trip |
| Join queue | Same add-booking endpoint; inspect `inQueue` | Official client/schema + offline behavioral tests |
| Claim offered queue place | POST `/api/memberapi/workoutBooking/join?workout=...` | Official client/schema + offline behavioral tests |
| Leave queue | Remove endpoint, guarded against promotion | Official client/schema + offline behavioral tests |
| Valid / historical memberships | GET `/api/memberapi/trainingcard/get/valid` or `/all` | Valid cards live; historical contract tested offline |
| Create own account | POST `/api/memberapi/add` | Schema + offline tests; no real accounts created |
| Update own profile | POST `/api/memberapi/change` | Schema + offline tests; no real profile changes |
| Request reset email | POST `/api/memberapi/requestResetPassword?mail=...` | Schema; no real reset emails sent |
| Calendar subscription | Member `icalendarid` → `/api/public/icalendar/get?id=...` | Schema + offline tests; private URLs excluded from default output |
| Logout | POST `/api/memberapi/logout` | Live |
| Inspect scanned QR resource | GET `/api/door/scan?data=...` | Official member client + live read-only lookup on 2026-11-03 |
| Register own QR entry | POST `/api/memberapi/entry/qrcode` with JSON `qrcode_entry_id` | Official schema/client + offline tests; no real check-in submitted |

The live tenant accepted member login/read/booking/cancellation without an integration key. Official integration documentation also describes `Authorization: Zoezi <key>`; `ZOEZI_API_KEY` supports registered integrations. Do not assume other tenants have identical access policies.

## Important semantics

- Member authentication uses a session cookie, not OAuth refresh tokens. An expiry far in the future in a cookie is not proof of perpetual server-side validity. Recovery uses a fresh password login from supplied environment/keychain credentials.
- `bookings/get` can include queue entries with `booked: true`; `inQueue` determines whether the member actually has a confirmed place. Queue position is the `queue` field when available.
- `numBooked` includes queued members in the official client: confirmed occupancy is `numBooked - numQueue`.
- Booking and cancellation use workout IDs; returned booking IDs are not cancellation IDs.
- The same add API is used for queue entry. A race can produce a confirmed place instead. The CLI reports readback, not the caller's predicted state.
- `workoutBooking/join` is a queue **claim**, not initial queue joining.
- Queue leaving checks fresh status first. The remove API has no documented atomic queue-status precondition; promotion can race that check and write. Do not describe the client-side check as a server transaction.
- Cancellation timing comes from the workout category's `unregisterHoursBefore`; booking windows use `registerDaysBefore`, `registerHoursBefore`, `allowBookingAfterStarted`, `dropin` and the facility release time. No global `canCancel` setting is assumed to describe workout cancellation policy.
- All writes use existing membership coverage and leave authoritative eligibility/weekly limits with Zoezi. The CLI does not purchase fallback coverage.
- Dates from the API are local naive timestamps. The CLI applies the configured IANA zone and emits explicit offsets. Windows are filtered locally to avoid inclusive upstream boundary surprises.
- A network error or 5xx after a write may mean the write happened. Re-read before any subsequent action; never blindly retry.

## Scope

Coverage targets personal class booking, QR entry and onboarding, not every administrative API. No staff access, other-member management, purchases, payments, legal acceptance or door control is implemented. Waitlist behavior was tested with realistic offline fixtures, not by occupying real classes or manufacturing a full queue. Calendar connector writes require the user's selected provider and calendar; an export/plan alone is not synchronization.

## QR entry (2026-11-03, API 11.5)

The [official member client](https://crossfitvastkusten.zoezi.se/mypages) first resolves a scanned value with `GET /api/door/scan?data=...`. Its QR scanner dispatches `resourceType: qrcode_entry` to `/api/memberapi/entry/qrcode`, and `resourceType: door` to a separate door-opening API. The CLI implements the QR-entry branch only and refuses other resource types; it does not try an alternate mutation if the requested branch fails. A live read-only lookup at CrossFit Västkusten resolved the user-supplied code to an active QR-entry resource, with `punch: false`. The real QR value is not stored in this repository or the skill.

The scan lookup is observed in the current official client but absent from the developer schema. The schema documents the write's `QREntryData` request (`qrcode_entry_id`, optional other-member `user_id`); the CLI omits `user_id` and uses the authenticated member. The schema's response is labelled `EntryAddData`, but both official clients check the response's boolean `success`. The CLI requires exactly that explicit acknowledgement, surfaces rejection reasons and allowlists result fields rather than exposing the full member/card payload.

QR entry is distinct from `/memberapi/entry/checkin/add`, resource-booking-service check-in and staff attendance APIs. Public `checkin: false` is not a gate for the QR branch. No member-scoped entry-history lookup or idempotency token is documented for this operation. The CLI therefore never retries a submitted entry, even on a 401, and reports network/5xx/ambiguous-response outcomes as `checkin_unknown`. A successful QR entry is not independent proof that a particular workout booking is marked present. The CLI returns `attendance_verified: false` and does not inspect bookings as a substitute for entry verification.
