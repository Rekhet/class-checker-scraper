# User timers

The scheduled full-update service (06:00, 12:00, 18:00 daily since
2026-09-27; it was hourly on weekdays 09–20) does **not** crawl sugang. The 10-minute 인원
pass runs on GitHub-hosted runners (cron-job.org -> `collect-counts.yml` ->
`scraper/cloud_collect.py` -> cloud Turso), so `scripts/update.sh` merges those
samples (`scraper/pull_counts.py`), copies the newest sample onto the catalog's
volatile columns (`python -m scraper.sync_counts`), and publishes. A missing
`turso-remote.env` or a failed pull warns and **still publishes** — local
workers may have collected this hour, and pending trend commits in `web/` are
pushed by this run alone — and the run then exits nonzero only if the newest
sample is also stale for an open collection window. Set `UPDATE_CRAWL=1` for an
intentional catalog/평가방식 refresh, which only a local crawl can collect.

### Temporary: collecting locally during a cloud outage

When the cloud Turso database is unusable (2026-09-04: the free plan's
read/write quota ran out mid-semester, resetting only on 10-01), enable the
legacy 10-minute worker so 수강인원 keeps being collected from this machine:

```sh
systemctl --user enable --now class-checker.update-counts.timer
# drop-in: [Service] Environment=COUNT_MODE=enrollment
#   collect.env's COUNT_MODE=cart is for the 장바구니 window
```

It is still window-gated by `collect.env`, so it costs nothing off-season, and
the scheduled full update publishes what it collects. Disable it again once the
remote collector is running, and keep the `collect-counts` workflow disabled
(`gh workflow disable collect-counts`) while the database refuses writes, so
every dispatch does not turn into a failure mail.

`class-checker.update.service` runs `scripts/wait-online.sh` as `ExecStartPre`.
The timer is persistent, so a missed activation is replayed the moment the
machine resumes — seconds before NetworkManager has a link, which used to kill
the run outright. The script waits (best-effort `nm-online`, then reachability
probes of github.com and the Turso host read from `turso-remote.env`) up to
`WAIT_ONLINE_TIMEOUT` seconds, default 300; a still-dead network fails the
pre-start and leaves the next scheduled activation to retry.

The full update service and the fast cart/enrollment/trend services all use the same
host-local `data/.crawl.lock`. The lock is held across the database operation,
JSON export, and Git publication, so a counts pass cannot read a half-rebuilt
term or publish over another update.

Publication retries a failed push (up to three attempts, growing backoff, and a
larger `http.postBuffer`): the trend file was ~27 MB until the v2 format
(2026-09-27, now ~0.34 MB) and GitHub occasionally dropped the upload with
`RPC failed; HTTP 408`. The commit is already made when that happens, so
without the retry the site silently stayed behind until the next run.
A scheduled run without a crawl exports only the collected term
(`EXPORT_SCOPE=current` → `export_json.py --current`): its class file, live
trend, and index entry. Past terms and `explore-index.json` change only with a
catalog crawl, which exports everything. The live trend resumes from a local
checkpoint (`data/trend_cache/`, the per-class state at the end of the last
frozen chunk, validated by the sample count up to it and rolled forward when
the window moves) instead of replaying ~7M samples: a run went from ~45 s to
~19 s including pull and push (measured 2026-09-28).
Unchanged files are not rewritten: frozen trend chunks stay as written, and
`explore-index.json` keeps its old `generated` stamp when nothing else in it
moved (it used to be re-committed every hour for the stamp alone).

The bounded cart/enrollment/trend services commit each generated trend update in the
`web/` repository but deliberately does not push it. The
`class-checker.update.timer` runs the full update and is the scheduled push
boundary, so it publishes the accumulated local commits at each run. A
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
for f in class-checker-alert@.service class-checker.backup.service \
         class-checker.backup.timer; do
  install -Dm644 "systemd/$f" "$HOME/.config/systemd/user/$f"
done
systemctl --user daemon-reload
systemctl --user enable --now class-checker.update.timer class-checker.backup.timer
```

Restarting `class-checker.update.timer` after changing its schedule replays a
missed slot at once (`Persistent=true`), which is a normal run.

## Failure alerts

`class-checker.update.service` and `class-checker.backup.service` carry
`OnFailure=class-checker-alert@%n.service`, which runs
`scripts/notify-failure.sh <unit>`:

- the unit's last 400 journal lines go to
  `data/logs/failures/<unit>-<time>.log` (mode 600, ignored by git, newest 50
  kept) and **nowhere else**;
- a GitHub issue titled `ops-alert: <unit> failed` (label `ops-alert`) is
  opened on `Rekhet/class-checker-scraper` — or, while one for the same unit is
  open, commented on — with the time, result, exit status, and the local log's
  path. No log text is uploaded: the repository is public. GitHub mails the
  owner for new issues and comments.

Close the issue once the cause is fixed. `gh` must stay authenticated for the
user; if it is not, the alert unit itself fails and only the journal has it.
The update service also has `TimeoutStartSec=45min`, so a hung pull or push
can no longer hold `data/.crawl.lock` forever. Verified 2026-09-27 with a
transient unit running `/bin/false`: the first failure opened issue #1, the
second commented on it (closed as a test).

## Weekly backup

`class-checker.backup.timer` (Sundays 04:30, replayed after downtime) runs
`scripts/backup-db.sh`: an online snapshot of `data/turso.db` through the
SQLite backup API, `PRAGMA quick_check`, then `xz -9e` into
`~/.backup/class-checker/turso-<time>.db.xz` (mode 600). The newest three are
kept (`BACKUP_KEEP`). `count_samples` cannot be re-collected from SNU, so this
is the only copy of the enrollment history outside the cloud collector's
database, which holds only the current semester. Measured on the 1.37 GB
catalog: zstd -19 85 MB (4 min), zstd --ultra -22 84 MB (13 min), xz -9e
61 MB (6.6 min). Restore with `xz -dc <archive> > data/turso.db` while no
unit is running.

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
`*_WINDOWS` values in `collect.env` remain the canonical runtime configuration.
The GitHub collector reads `COUNT_YEAR`/`COUNT_SEM` from the same file (since
2026-09-27; it used to hard-code 2026 fall). A new semester still takes more
than that one edit — see "Semester rollover" below.

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
`count_samples` untouched; its `init_schema` first rebuilds a cloud `class_slots`
that predates the per-meeting `room` column, which is one more full write of
that table). `scraper.reseed_roster` does not migrate the cloud: it sends one
room-less row per meeting time.

```sh
set -a; . ./turso-remote.env; set +a
.venv/bin/python scraper/migrate_to_turso.py --src data/turso.db
```

`make migrate-remote` targets the separate production database in
`prod-admin.env`, not the collector's.

## Catalog drift re-crawl

The catalog is not crawled on a schedule. Every collector pass compares the
live search with the seeded roster by (sbjt_cd, lt_no) and records any
difference in the cloud table `roster_drift` (`drift: +N [...] / -M [...]` in
the runner log). `update.sh` pulls those rows and runs
`python -m scraper.roster_drift --check`; it crawls only when the drift

- has been seen on at least `DRIFT_MIN_PASSES` (3) passes and is still present
  in the newest one,
- was not already handled, and
- comes after the term's last `CART_WINDOWS`/`ENROLL_WINDOWS` day (during
  registration and change periods the roster churns by design).

Then it runs `make refresh … COLLECT=catalog,enrollment,grading`, replaces the
term's cloud roster (`python -m scraper.reseed_roster`, one transaction,
refuses a roster that shrank below 90%), marks the drift handled, and exports
every term. A failure is retried next run and fails the run (alert issue).

To accept a drift without crawling:
`python -m scraper.roster_drift --year … --semester … --mark-handled <signature>`
(the signature is in the `--check` output). On 2026-09-28 the drift present
since the last crawl — live `375.803(042)` and `552.439(002)` missing from the
catalog — was marked handled this way as the baseline, per the owner (no more
catalog crawls this semester).

## Semester rollover

Nothing below happens on its own. In order:

1. Read the new term's schedule on the sugang landing page (co010) and rewrite
   the `collect.env` header comment and `CART_WINDOWS`, `ENROLL_WINDOWS`,
   `ENROLL_SLOW_WINDOWS`. Set `COUNT_YEAR`/`COUNT_SEM`; both the local scripts
   and the GitHub collector read them from there.
2. Crawl the new term's catalog locally:
   `UPDATE_CRAWL=1 UPDATE_YEAR=<year> UPDATE_SEM=<sem> ./scripts/update.sh`.
3. Re-seed the collector's cloud roster from the local catalog (the command
   above). Until this is done every runner pass fails with "cloud roster ...
   is empty" — by design, instead of recording empty passes.
4. If a cart window is configured, arm the bounded cart units (below); cart is
   never collected on the GitHub runner.
5. Commit and push `collect.env`. Pushing `main` is what deploys it to the
   runner. Confirm the next runner pass prints `coverage: 100.00%` or close to it.
6. Run `systemctl --user daemon-reload` if any unit file changed.

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
worker priority over the scheduled full-update timer; overlapping services still
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
