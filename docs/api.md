# API coverage and observed behavior

Primary sources: [Zoezi API documentation](https://developer.zoezi.se/apidoc/), its [machine-readable schema](https://developer.zoezi.se/api/api/get), and the official member web client's request behavior. Inspection and live verification: 2026-10-29, API 11.4. Paths other than login use the facility's current API; login explicitly uses the documented v8.0 JSON contract.

| CLI capability | Method and path | Verification |
|---|---|---|
| Setup, fields, doctor, rules | GET `/api/public/settings/get` | Live |
| Password login / refresh | POST `/api/v8.0/memberapi/login` | Live login; automatic recovery also offline tested |
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

The live tenant accepted member login/read/booking/cancellation without an integration key. Official integration documentation also describes `Authorization: Zoezi <key>`; `ZOEZI_API_KEY` supports registered integrations. Do not assume other tenants have identical access policies.

## Important semantics

- Member authentication uses a session cookie, not OAuth refresh tokens. An expiry far in the future in a cookie is not proof of perpetual server-side validity. Recovery uses a fresh password login from supplied environment/keychain credentials.
- `bookings/get` can include queue entries with `booked: true`; `inQueue` determines whether the member actually has a confirmed place. Queue position is the `queue` field when available.
- `numBooked` includes queued members in the official client: confirmed occupancy is `numBooked - numQueue`.
- Booking and cancellation use workout IDs; returned booking IDs are not cancellation IDs.
- The same add API is used for queue entry. A race can produce a confirmed place instead. The CLI reports readback, not the caller's predicted state.
- `workoutBooking/join` is a queue **claim**, not initial queue joining.
- Cancellation timing comes from the workout category's `unregisterHoursBefore`; booking windows use `registerDaysBefore`, `registerHoursBefore`, `allowBookingAfterStarted`, `dropin` and the facility release time. No global `canCancel` setting is assumed to describe workout cancellation policy.
- All writes use existing membership coverage and leave authoritative eligibility/weekly limits with Zoezi. The CLI does not purchase fallback coverage.
- Dates from the API are local naive timestamps. The CLI applies the configured IANA zone and emits explicit offsets. Windows are filtered locally to avoid inclusive upstream boundary surprises.
- A network error or 5xx after a write may mean the write happened. Re-read before any subsequent action; never blindly retry.

## Scope

Coverage targets personal class booking and onboarding, not every administrative API. No staff access, other-member management, purchases, payments, legal acceptance, door control or check-in is implemented. Waitlist behavior was tested with realistic offline fixtures, not by occupying real classes or manufacturing a full queue. Calendar connector writes require the user's selected provider and calendar; an export/plan alone is not synchronization.
