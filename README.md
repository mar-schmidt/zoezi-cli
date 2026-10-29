# zoezi-cli

A standalone, JSON-first CLI and agent skill for a member's own Zoezi class bookings. Inspired by the command contract of [mindbody-cli](https://github.com/mar-schmidt/mindbody-cli); it does not replace or depend on that project.

Supports password-based, browserless login and unattended re-login, schedules, bookings, cancellations, queues, first-come queue claims, memberships, member onboarding and calendar handoff. No BankID implementation or browser automation is required.

## Install

Download the wheel from [Releases](https://github.com/mar-schmidt/zoezi-cli/releases) and install it:

```bash
python -m pip install ./zoezi_cli-*.whl
zoezi --help
```

Alternatively, with repository access:

```bash
pipx install 'git+https://github.com/mar-schmidt/zoezi-cli.git@master'
```

Python 3.11+; Linux and macOS. The runtime uses the Python standard library. Install the `keyring` extra if you want password storage in your OS keychain. Use environment secrets on a headless host without a keychain.

## Set up an existing member

```bash
zoezi --url https://your-box.zoezi.se setup --username you@example.com
zoezi doctor
zoezi auth login                 # hidden local password prompt
zoezi auth status
zoezi memberships
zoezi account fields             # facility-specific required fields and terms
```

The URL can include `/mypages`; setup stores its HTTPS origin. Choose another IANA zone with the global `--timezone` option; the default is `Europe/Stockholm`. Global options go **before** the command. The facility must enable e-mail/password login. The CLI never switches to BankID.

For an unattended host, supply `ZOEZI_USERNAME` and `ZOEZI_PASSWORD` through your secret manager. `auth login --password-stdin` also accepts a single password line, without putting it in process arguments. To save a password for future automatic recovery, explicitly choose `auth login --save-credentials` with an available OS keychain. Passwords are never written into the session/config files.

Member sessions are stored atomically with file mode `0600` under `~/.config/zoezi-cli/` (directory mode `0700` for newly created directories). Complete CLI commands are locked, including authentication and booking readback, so concurrent jobs cannot race locally. A rejected session is renewed once using configured credentials. A 403 permission denial is not a reason to keep logging in. Use `--no-auto-login` to disable recovery.

## Classes, bookings and queues

```bash
zoezi status
zoezi schedule --date 2026-11-04 --days 7
zoezi schedule --date 2026-11-04 --name 'Group' --bookable-only
zoezi workout 123
zoezi bookings list --date 2026-11-03 --days 7

zoezi bookings create --workout 123 --dry-run
zoezi bookings create --workout 123
zoezi bookings cancel --workout 123 --dry-run
zoezi bookings cancel --workout 123

zoezi waitlist list
zoezi waitlist join --workout 123 --dry-run
zoezi waitlist join --workout 123
zoezi waitlist claim --workout 123 --dry-run
zoezi waitlist claim --workout 123
zoezi waitlist leave --workout 123 --dry-run
zoezi waitlist leave --workout 123
```

Read the actual workout ID from the schedule. Dates in examples are illustrative. The same workout ID is used for booking and cancellation. Booking uses an existing training card; the CLI does not buy anything or fall back to a payment method.

- `join` enters the queue when a class is full; `claim` takes an offered available place from an existing queue entry. Zoezi's corresponding API endpoint names are different from those CLI verbs.
- Results distinguish `confirmed`, `waitlisted` and `absent`. Queue positions are included when Zoezi returns them. `waitlist leave` refuses when its fresh preflight finds a confirmed reservation. Zoezi exposes no conditional queue-only cancellation, so an automatic promotion can still race the subsequent server request.
- Date windows are half-open: `--date 2026-11-03 --days 2` covers November 3 and 4. Offsets are explicit, including daylight-saving transitions. Unknown capacity remains `null`.
- `--dry-run` performs reads, never a speculative booking. It is a preflight, not an upstream reservation or guarantee.
- Booking an already confirmed class and removing an absent reservation are no-ops. Writes are read back before success is reported. A lost response is reconciled; timed-out writes are never repeated automatically.
- Cancellation past the public deadline or with unknown policy requires `--allow-late`. Public rules may not expose every financial or warning consequence. Zoezi remains authoritative for training-card eligibility and booking limits.

## Onboarding commands

`profile`, `profile update --data-file <file> --dry-run`, `memberships --all`, `account fields`, `account create --data-file <file> --dry-run` and `auth reset-password --dry-run` cover profile inspection, facility field discovery, optional self-registration and reset-email requests. Use `-` instead of a data filename to read JSON from stdin. Only run the non-dry-run form when the user requested that action. Do not commit account-data files.

Self-registration must be enabled by the facility, and required/changeable fields vary. The CLI does not silently accept legal terms, buy cards, pay bills, manage other people, check in or open doors. Pending terms and membership migration can require the facility's normal onboarding flow.

## Family calendar

```bash
zoezi --url https://your-box.zoezi.se setup --calendar-id '<family-calendar-id>'
zoezi calendar events --date 2026-11-03 --days 14
zoezi calendar export --date 2026-11-03 --days 14 --output bookings.ics
zoezi calendar plan --existing-file existing-events.json --date 2026-11-03 --days 14
zoezi calendar feed             # availability only
```

The agent skill uses your connected calendar provider to create/update/delete matching events. The CLI produces event data, a reconciliation plan or an ICS snapshot; **it does not itself write to Google/Apple/Outlook calendars**. Configure the intended shared family calendar once. Each event has a stable source key containing tenant, member and workout ID. Reconciliation ignores unrelated events and dates outside the requested window.

Queue entries are excluded unless `--include-waitlist` is chosen; those events are tentative. `calendar feed --reveal` deliberately reveals Zoezi's private subscription URL. Treat that URL as a credential. Snapshot export is not a live subscription, and a calendar failure does not undo a successful workout booking.

## Agent skill and releases

The source skill is in [`.agent/SKILL.md`](.agent/SKILL.md), with UI metadata and a detailed workflow reference. Every push to `master` runs tests on Python 3.11/3.12/3.13, validates the skill and executable examples, builds the CLI **and skill from the same commit**, verifies the artifacts and publishes a GitHub Release. No Zoezi credentials or custom repository secrets are needed for CI.

Each release has a distinct PEP 440 version (`0.1.0.post<workflow-run-number>`) shared by its CLI wheel and skill manifest. Assets include:

| Asset | Contents |
|---|---|
| `zoezi_cli-*.whl` | Installable CLI |
| `zoezi_cli-*.tar.gz` | Source distribution |
| `zoezi-cli.skill` | Installable skill ZIP, with source commit, version and generated CLI contract |
| `zoezi-cli-skill.zip` | Same skill, for agents expecting a ZIP extension |
| `release-manifest.json` | Exact version, source commit and artifact digests |
| `SHA256SUMS` | Checksums for the release files |

Failed checks prevent publication. No workflow concurrency group drops intermediate master commits. Re-running the same workflow updates only the release for that exact source commit. GitHub's built-in `GITHUB_TOKEN` publishes the assets.

GitHub-hosted runner access and sufficient Actions allowance are required. An account budget policy can prevent jobs from starting. The local development/build commands below reproduce validation without changing billing or repository visibility.

## Output and environment

Success: one JSON object on stdout, `{"ok":true,...}`. Errors: one object on stderr, `{"ok":false,"error":"...","code":"...","details":{...}}`. Branch on `code`, not message prose. Help/version are text.

| Exit | Meaning |
|---|---|
| 0 | Success |
| 1 | Usage, validation or booking precondition |
| 2 | Authentication or permission |
| 3 | Upstream rejection or rate limit |
| 4 | Network, lock timeout or uncertain mutation outcome |

| Variable | Purpose |
|---|---|
| `ZOEZI_URL` | HTTPS facility origin |
| `ZOEZI_USERNAME` | Member login |
| `ZOEZI_PASSWORD` | Unattended password, supplied securely |
| `ZOEZI_CONFIG_DIR` | Isolated configuration/session directory |
| `ZOEZI_TIMEZONE` | IANA timezone |
| `ZOEZI_API_KEY` | Optional registered integration key, if required by the tenant |

Use `auth logout` to revoke the current session and clear local state. Add `--forget-credentials` to remove the saved keychain password. Never share session files.

## Development and verification

```bash
python -m pip install -e '.[dev]'
python -m unittest discover -s tests -v
ruff check src tests scripts
python scripts/skill_package.py --check
python scripts/build_release.py --commit "$(git rev-parse HEAD)"
python scripts/verify_release.py
```

See [docs/api.md](docs/api.md) for the API coverage and verification boundary, and [docs/skill-evaluation.md](docs/skill-evaluation.md) for reproducible offline skill scenarios. Tests contain synthetic data and never book a real class. A live booking/cancellation round trip was separately verified with explicit permission on 2026-10-29, preserving the pre-existing reservation.

Independent project; not affiliated with Zoezi AB. See [DISCLAIMER.md](DISCLAIMER.md). MIT licensed.
