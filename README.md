# Class Checker automation

This repository owns the private crawler, collection configuration, worker
scripts, systemd units, tests, and operational documentation for Class Checker.

The `web/` directory is intentionally excluded from this repository. It is an
independent public Git repository used for GitHub Pages deployment and contains
only the deployable site and generated public data. The publisher operates on
that separate worktree explicitly.

The crawler accepts one shared collection interface:
`--collect catalog,enrollment,cart,grading`. The scheduled full update uses
`catalog,enrollment,grading`, while a bounded cart window uses `cart`; selecting
both live metric groups still performs one search pass per term.

Live search results can carry a different `subh_cd` from the Excel catalog. The
counts overlay resolves an exact identity first, then safely falls back to the
unique `(sbjt_cd, lt_no)` catalog identity; ambiguous candidates are left
untouched. Audit the active catalog with:

```sh
./scripts/audit-cart-counts --year 2026 --term fall --live
```

The audit is read-only. A stored `NULL` is expected outside a cart window; with
`--live`, the report distinguishes site-side NULLs from live non-negative values
that should have been stored.

## Configuration

Review `collect.env` for the active semester, timezone, collection mode, and
collection windows. Use `collect.env.example` as the schema for a future
semester. Production Turso credentials and OAuth secrets remain local and are
ignored by Git.

## Start a bounded cart window

```sh
./scripts/start-cart-window \
  --start-date 2026-08-04 \
  --year 2026 \
  --semester fall \
  --timezone Asia/Seoul \
  --disable-broad-timer
```

Use `--dry-run` to print the exact schedule without changing systemd. The
launcher creates a collector timer for every ten minutes through the next
day's 16:00 run and a cleanup timer at 16:10.

## Start the bounded enrollment window

The first-come registration days use a separate enrollment-only timer:

```sh
./scripts/start-enrollment-window \
  --dates 2026-08-07,2026-08-10,2026-08-11 \
  --start-time 08:30 \
  --end-time 16:30 \
  --year 2026 \
  --semester fall \
  --timezone Asia/Seoul \
  --disable-broad-timer
```

It runs every ten minutes, inclusive, on exactly those three dates (147 runs
total), records enrollment counts rather than cart counts, and cleans up at
16:40 on 2026-08-11. Use `--dry-run` before installation to inspect the exact
calendar schedule.

## Runtime safety and recovery

Every database, export, and publication writer cooperates through the advisory
`flock(2)` at `data/.crawl.lock`. The zero-length file is intentionally
persistent; the kernel lock held on its open file descriptor is what protects
the data. Successful completion, a failed child process, an exception, or an
interrupted process releases that lock. The user systemd services use
`KillMode=control-group`, so a systemd interruption terminates the worker and
its child processes together.

If the computer sleeps while a writer owns the lock, the suspended process
keeps the lock. A waiting worker waits up to `CRAWL_LOCK_TIMEOUT` (900 seconds
by default), then exits with status 75; it is not automatically retried. The
bounded cart timer does not replay missed activations after wake, while the
full-update and cleanup timers may receive their configured persistent catch-up
activation.

Repeated activation of one oneshot service does not run that service in
parallel. Full-update, cart, and cleanup services are separate units, so they
may activate independently, but the shared lock serializes their writes. A
later worker waits for the lock and follows the same 900-second timeout; no
durable run queue is created.

Before cleanup removes a bounded window's generated state, it requires the
collector service to be inactive, to report `Result=success`, and to have an
actual `ExecMainStartTimestamp`. A failed, unavailable, or never-run collector
therefore leaves the window state in place and returns failure. Automatic retry
of that failed window remains deferred.

The remaining hardening items are recorded in the [implementation plan](docs/superpowers/plans/2026-08-03-cart-window-systemd-timer.md#post-implementation-audit-2026-08-04): lock-wait retry policy, automatic retry after failed cleanup validation, forced-count cart semantics, transactional rebuild recovery, publication staging isolation, and direct-wrapper process-group handling.

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
systemd-analyze verify systemd/class-checker.update.service \
  systemd/class-checker.update.timer \
  systemd/class-checker.update-counts.service \
  systemd/class-checker.update-counts.timer \
  systemd/class-checker.enrollment@.service \
  systemd/class-checker.enrollment-cleanup@.service
```
