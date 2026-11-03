# Skill evaluation

The skill was validated with Skill Creator's frontmatter validator, executable example parsing, generated CLI command metadata and separate agent runs using the real CLI command handler against an offline transport. The agents received the skill and task-local inputs, without expected outputs or production credentials.

## Reproduce offline scenarios

Install the development environment and put a `zoezi` wrapper on a temporary PATH that executes `tests/agent_sandbox.py` with the installed Python. Set `ZOEZI_SANDBOX_STATE` to a fresh directory for each scenario. The adapter uses synthetic data, a fixed clock and a full workout, persists only synthetic reservations and records method/path/parameters/body in `calls.jsonl`, with QR values redacted. It performs no network access.

1. Ask the agent to join the queue for Group Class on November 4 at 17:00, then leave it as a test, preserving the November 3 booking. Observed: preflights, one queue join, one queue removal, queue empty afterwards, original booking preserved.
2. Provide existing external calendar objects, including one managed event with an outdated title and an unrelated dinner. Ask for a calendar reconciliation plan for November 3–4, without external writes. Observed: one managed-event update, no duplicates/deletions, dinner preserved, and no claim that a calendar write occurred.
3. Ask the agent to check in while at the synthetic facility, with the QR value supplied by the wrapper's `ZOEZI_QR_CODE`. Set `ZOEZI_SANDBOX_ENTRY_FAILURE=network_error` to simulate a lost response after submission. Observed on 2026-11-03: the agent inspected the resource, previewed the authenticated operation, submitted once, reported `checkin_unknown`, requested app/reception verification and made no booking changes or retry. The command trace confirmed exactly one entry POST; no real check-in or credentials were used.

Automated tests additionally exercise a queue promoted before leaving, a queue join that becomes confirmed, claim semantics, expired authentication, forbidden access, lost responses, failed readback, late cancellation, concurrent state access, date windows and calendar identity.

Live booking/cancellation validation is separate and requires explicit user authorization for the exact test workout. Always snapshot existing reservations and clean up only the test-created reservation; do not alter existing bookings to satisfy a membership limit.
