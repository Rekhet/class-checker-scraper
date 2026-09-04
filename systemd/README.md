# User timers

The hourly full-update service does **not** crawl sugang. The 10-minute 인원
pass runs on GitHub-hosted runners (cron-job.org -> `collect-counts.yml` ->
`scraper/cloud_collect.py` -> cloud Turso), so `scripts/update.sh` merges those
samples (`scraper/pull_counts.py`), copies the newest sample onto the catalog's
volatile columns (`python -m scraper.sync_counts`), and publishes. A missing
`turso-remote.env` or a failed pull fails the run: it is the only source of
fresh data, and republishing yesterday's numbers silently would hide the
outage. Set `UPDATE_CRAWL=1` for an intentional catalog/평가방식 refresh, which
only a local crawl can collect; both failures then degrade to warnings.

`class-checker.update.service` runs `scripts/wait-online.sh` as `ExecStartPre`.
The timer is persistent, so a missed activation is replayed the moment the
machine resumes — seconds before NetworkManager has a link, which used to kill
the run outright. The script waits (best-effort `nm-online`, then reachability
probes of github.com and the Turso host read from `turso-remote.env`) up to
`WAIT_ONLINE_TIMEOUT` seconds, default 300; a still-dead network fails the
pre-start and leaves the next hourly activation to retry.

The full update service and the fast cart/enrollment/trend services all use the same
host-local `data/.crawl.lock`. The lock is held across the database operation,
JSON export, and Git publication, so a counts pass cannot read a half-rebuilt
term or publish over another update.

The bounded cart/enrollment/trend services commit each generated trend update in the
`web/` repository but deliberately does not push it. The hourly
`class-checker.update.timer` runs the full update and is the scheduled push
boundary, so it publishes the accumulated local commits once per hour. A
manual `scripts/publish.sh counts` or `trend` invocation follows the same
commit-only policy; full publication retains the `PUBLISH_PUSH=0` escape hatch.

Install or refresh the user units with:

```sh
install -Dm644 systemd/class-checker.update.service \
  "$HOME/.config/systemd/user/class-checker.update.service"
install -Dm644 systemd/class-checker.update.timer \
  "$HOME/.config/systemd/user/class-checker.update.timer"
install -Dm644 systemd/class-checker.update-counts.service \
  "$HOME/.config/systemd/user/class-checker.update-counts.service"
install -Dm644 systemd/class-checker.update-counts.timer \
  "$HOME/.config/systemd/user/class-checker.update-counts.timer"
systemctl --user daemon-reload
systemctl --user enable --now class-checker.update.timer
```

Each worker service creates the private user-runtime directory
`%t/class-checker` and exports it as `TMPDIR`. Playwright therefore keeps its
temporary Chromium profiles out of the shared `/tmp` filesystem, where
unrelated stale files or quota pressure can otherwise make a browser launch
fail. systemd removes the runtime directory when the oneshot service exits.

The legacy counts timer is deliberately broad on weekdays during 09:00–20:50,
but it must remain disabled when a bounded cart or enrollment window is installed. The
`--windowed` pass checks `collect.env` before opening a SNU session, so the
legacy worker is inactive outside the configured cart/enrollment windows.
Change `COUNT_MODE` to `counts` in `collect.env` when an intentional manual or
legacy run should collect both live metric groups. The preferred interface is
the crawler's explicit
`--collect` selection:

```text
catalog     Excel catalog and timing rebuild
enrollment  live applied/quota/enrolled overlay and enrollment samples
cart        live 장바구니 overlay and cart samples
grading     평가방식/전환가능여부 sweep
```

An opt-in `UPDATE_CRAWL=1` full update uses `catalog,enrollment,grading`; it
deliberately excludes `cart`. A bounded cart worker uses `cart`, while a
bounded enrollment worker uses `enrollment`. When both live groups are
selected, they share one search pass and do not issue duplicate count requests.
`COUNT_YEAR`, `COUNT_SEM`, `COUNT_MODE`, `COLLECTION_TIMEZONE`, and the
`*_WINDOWS` values in `collect.env` remain the canonical runtime configuration;
edit that file for a future semester, then run `systemctl --user daemon-reload`.

The full-update wrapper reads `COUNT_YEAR` and `COUNT_SEM` from the same file.
For an intentional one-off full refresh, `UPDATE_CRAWL=1` enables the crawl and
`UPDATE_YEAR`, `UPDATE_SEM`, and `UPDATE_COLLECTIONS` may override the
configured scope. `UPDATE_COLLECTIONS` is fail-closed if it includes `cart`;
cart collection belongs to the bounded worker.

Only `count_samples` crosses the cloud boundary, so without a crawl the catalog
itself is frozen: new/renamed/폐강 classes, professor, room, language, timing,
and 평가방식 stay as the last crawl left them, and a class missing from the
cloud roster is never counted there. Run `UPDATE_CRAWL=1 ./scripts/update.sh`
after a timetable change. If the roster itself changed, re-seed the collector's
cloud catalog too (this refills terms/classes/class_slots/crawl_runs and leaves
`count_samples` untouched):

```sh
set -a; . ./turso-remote.env; set +a
.venv/bin/python scraper/migrate_to_turso.py --src data/turso.db
```

`make migrate-remote` targets the separate production database in
`prod-admin.env`, not the collector's.

## Bounded two-day cart collection

Use the launcher for a cart window instead of running the broad count timer
during the cart period:

```sh
./scripts/start-cart-window \
  --start-date 2026-08-04 \
  --year 2026 \
  --semester fall \
  --timezone Asia/Seoul \
  --disable-broad-timer
```

The launcher renders date-specific user timers and a per-window environment
file. The collector runs at ten-minute boundaries from 09:00 on the start date
through the final 16:00 run on the next date. The cleanup timer runs at 16:10,
stops future collector activations, waits for the collector and
`data/.crawl.lock` to become idle, then removes only that window's generated
timer files. The reusable `@.service` templates remain installed.

The old broad `class-checker.update-counts.timer` is intentionally not allowed
to run alongside a bounded window, because it would duplicate live-count
requests.
The launcher rejects that state unless `--disable-broad-timer` is supplied. It
also rejects a second overlapping bounded window and is safe to rerun for the
same start date. Use `--dry-run` to inspect a schedule without changing files
or systemd.

## Bounded multi-date enrollment collection

Use the enrollment launcher for first-come registration days. It renders one
date-specific user timer containing only the requested dates, plus one cleanup
timer after the final date:

```sh
./scripts/start-enrollment-window \
  --dates 2026-08-07,2026-08-10,2026-08-11 \
  --start-time 08:30 \
  --end-time 16:30 \
  --burst-minutes 30 \
  --burst-interval 5 \
  --interval 10 \
  --year 2026 \
  --semester fall \
  --timezone Asia/Seoul \
  --disable-broad-timer
```

The 2026-2 portal schedule lists 08:30–16:30 for all three dates. The timer
therefore creates seven five-minute burst runs from 08:30 through 09:00,
followed by ten-minute runs from 09:10 through 16:30: 52 runs per date, 156
total. It does not run on the weekend or on 8/12. Its environment selects
`COUNT_MODE=enrollment`, so samples record the live applied/enrolled group and
leave cart sampling disabled. Cleanup runs at 16:40 on 8/11, waits for the
final service and shared lock, and removes only the generated
enrollment-window files after the same success gate used by the cart cleanup.

The `--burst-minutes`, `--burst-interval`, and `--interval` options make the
cadence explicit for future semesters. This change does not give the bounded
worker priority over the hourly full-update timer; overlapping services still
serialize through `data/.crawl.lock` under the documented lock-wait policy.

Use `--dry-run` to inspect the exact schedule without writing units or starting
systemd. The launcher rejects duplicate or unsorted dates and a second
different enrollment window, and it is safe to rerun the same window.

## Lock, sleep, and overlap behavior

All cooperating writers use the advisory `flock(2)` at
`data/.crawl.lock`. The lock file itself remains on disk after a run; its open
file descriptor and kernel lock are the protection. A successful run, a
nonzero worker result, an exception, or process termination releases the lock.
The user services report `KillMode=control-group`, so systemd termination is
expected to terminate the wrapper and its child together.

When the computer sleeps, a suspended lock owner normally keeps its descriptor
and lock. A waiting worker can therefore remain blocked until the
`CRAWL_LOCK_TIMEOUT` default of 900 seconds, then exits with status 75. The
generated cart and enrollment timers use `Persistent=false`, so missed bounded
activations are not replayed after wake. The full-update and cleanup timers are persistent and
may receive a catch-up activation. Cleanup waits up to 1800 seconds for an
active collector or the shared lock; if that wait expires, it leaves the
window state for manual recovery. Before deleting the generated window files,
cleanup also requires the collector to report `Result=success` and to have a
non-empty `ExecMainStartTimestamp`; a failed, unavailable, or never-run
collector leaves the state in place and returns failure.

An activation request for a service that is already running is not executed in
parallel. The full, cart, enrollment, and cleanup services are distinct, so they can be
requested independently, but the shared lock serializes their database,
export, and publication work. A later worker waits and then exits 75 if the
first worker does not release the lock in time; the current design does not
queue an automatic retry.

The cart and enrollment services have a 20-minute `TimeoutStartSec`; cleanup has 35 minutes.
Systemd termination releases the lock, but interrupted work may be partial and
is not automatically retried. Directly signalling only a wrapper outside
systemd could leave an orphaned child; explicit process-group hardening is
deferred.

## Deferred hardening

The current design intentionally defers lock-wait retry/backoff, cleanup
automatic retry of a failed or never-run final sample, forced-count cart
semantics, transactional catalog rebuilds, isolation of unrelated pre-staged
publication changes, and recovery after long sleep or service timeout. The
implementation plan records the rationale and current audit state.
