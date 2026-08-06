# Implementation Plan: Date-Bounded Cart Collection Timer

## Status

Accepted and implemented. The blocker-remediation pass, date-bounded timer,
cart-free full-refresh defaults, shared lock audit, Result=success cleanup gate,
and operational documentation are complete in the root automation repository.
The August 4–5, 2026 window is installed; its final collector and scheduled
cleanup journal entries remain live verification items until those events occur.

The 2026-08-05 pre-final-window audit found two Chromium startup failures caused
by temporary profile I/O under the shared `/tmp` filesystem. All worker service
templates now allocate `%t/class-checker` and export it as `TMPDIR`; the
installed user units were refreshed and reloaded. The mitigation was verified by
the 01:00 collector run, which completed 8,625/8,625 classes and exported 96
trend samples.

Plan date: 2026-08-04; last operational audit: 2026-08-05

## Enrollment window extension (2026-08-06)

The live schedule table at `https://sugang.snu.ac.kr/sugang/co/co010.action`
was checked on 2026-08-06. It lists first-come registration on 2026-08-07,
2026-08-10, and 2026-08-11, each from 08:30 through 16:30. 2026-08-12 is
not a registration date and is intentionally excluded.

The reusable enrollment launcher and units are implemented:

- `scripts/start-enrollment-window` accepts an ascending list of dates and
  explicit ten-minute-aligned start/end times.
- `class-checker.enrollment@.service` runs `COUNT_MODE=enrollment`, while the
  generated environment restricts `ENROLL_WINDOWS` to the requested dates and
  clears `CART_WINDOWS`.
- One timer emits 49 inclusive runs per date (147 total); a persistent cleanup
  timer runs at 16:40 on the last date and uses the existing lock and
  `Result=success` cleanup gate.
- The canonical 2026-2 enrollment windows now represent 8/7 and 8/10–8/11 as
  disjoint dates, so the weekend is not treated as a collection window.

The schedule and no-start installation tests pass, and the service templates
pass `systemd-analyze verify`. The user units were installed and enabled on
2026-08-06 as
`class-checker.enrollment@20260807-20260810-20260811.timer` and
`class-checker.enrollment-cleanup@20260807-20260810-20260811.timer`; the live
collection and journal verification for 8/7–8/11 remain pending until those
dates execute.

Related audit note:
`docs/superpowers/audits/2026-08-03-class-checker-blockers.md`

## Blocker-remediation progress

As of 2026-08-04:

- [x] Git publication is rooted explicitly at `web/` and fails closed when
      publication is requested but that worktree is unavailable.
- [x] Year, semester, mode, timezone, and collection windows are sourced from
      the project-root `collect.env`; explicit environment overrides remain
      supported for tests and launches.
- [x] Active configuration and crawl documentation describe only the August
      4–5 cart window. July 28 is retained only as a historical “not collected”
      record where relevant.
- [x] Direct export and maintenance writers now acquire the same absolute
      `data/.crawl.lock` used by scheduled workers.
- [x] The automation layer is versioned in the root private Git repository;
      `web/` remains an independent public deployment repository.
- [x] Implement the date-bounded collector and cleanup timer lifecycle. The
      August 4–5, 2026 window is installed and active on the user systemd
      manager.
- [x] Normal full refresh entrypoints select `catalog,enrollment,grading` and
      exclude cart; cart collection is isolated to the bounded worker.
- [x] The shared lock lifecycle is covered for successful, failed, exceptional,
      and systemd-like interrupted workers.
- [x] The maintenance, README, and systemd operations documentation describe
      the lock, sleep/wake, overlap, timeout, and deferred-recovery behavior.
- [x] Cleanup requires an inactive collector with `Result=success` and a real
      `ExecMainStartTimestamp` before removing generated window state; failed,
      unavailable, or never-run collectors leave that state in place.
- [x] Worker service templates isolate Playwright's temporary browser profiles
      in the user runtime filesystem instead of shared `/tmp`; the installed
      units were refreshed and the post-fix collector run succeeded.

The root automation repository was initialized with commit `97bca30`. The
public `web/` repository remains separate and is intentionally ignored by the
root repository.

## Decision

Use a user-level systemd service/timer pair for each cart-collection window,
created and started by a small launcher script that accepts the window's start
date. Do not use a permanent broad 10-minute cart timer.

For a start date `D`, the collection window is:

- `D 09:00` through `D 23:50`, every ten minutes;
- `D+1 00:00` through `D+1 15:50`, every ten minutes;
- `D+1 16:00` as the final collection.

The launcher computes `D+1`, renders or activates date-specific timer instances,
and supplies the selected year and semester to the existing cart-only worker.
The schedule uses the crawl host's explicit local timezone, currently
`Asia/Seoul`, and a tight timer accuracy setting so the first run is anchored at
09:00.

Each window also gets a separate cleanup timer. It runs shortly after the final
16:00 trigger and:

1. stops the date-specific collector timer so no new run can start;
2. waits for an active collector to finish without killing it, using service
   state and/or the shared process lock;
3. requires the collector's final `Result=success` and an actual execution
   timestamp, otherwise reports failure and preserves the window state;
4. disables the collector and cleanup timer instances;
5. removes only those generated instances and reloads the user systemd manager.

Reusable unit templates remain installed for future semesters. Cleanup removes
only the one-window instances, not the templates or the installer itself.

## Why this design

Systemd supports multiple `OnCalendar=` directives, calendar ranges, and
repetition values. It can therefore execute the exact two-day schedule once the
launcher has calculated the dates. The launcher is responsible for date
arithmetic and safe unit-instance creation; the timer manager is responsible for
execution, service supervision, and cleanup scheduling.

The collector continues to use the shared `data/.crawl.lock`, so a full catalog
refresh, a cart pass, a server-triggered crawl, and publication cannot perform
conflicting writes at the same time. The collector remains window-aware as a
second defense, and its timer should not replay stale missed collection events.

The cleanup path must be idempotent and fail safely: if the collector remains
active past the wait timeout, cleanup reports failure and leaves enough state for
manual recovery rather than terminating the collector or silently deleting its
unit.

## Prerequisites: resolve before implementation

These items are gates, not part of the first timer implementation.

### Blocker 1: Correct Git publication root

- Change `scripts/publish.sh` to operate on the actual `web/` Git worktree.
- Use explicit `git -C "$WEB_ROOT"` operations or an equivalent `cd` boundary.
- Make missing Git publication fail visibly unless local-only publication was
  explicitly requested.
- Verify that both full and trend-only paths stage only their intended `web/data`
  files and that a test push can be disabled safely.

### Blocker 2: Centralize semester and window configuration

- Define one canonical configuration interface for year, semester, cart window,
  timezone, and collection mode.
- Remove the need to edit several hard-coded 2026/fall defaults when starting a
  future semester.
- Decide whether the launcher owns the cart window or whether it renders a
  temporary override for the existing `collect.env`-based gate.

### Blocker 3: Reconcile documentation with runtime configuration

- Remove the stale July 28 cart date from documentation.
- Ensure examples do not present conflicting semester dates.
- Document the exact start-date command and the cleanup lifecycle.

### Blocker 4: Version the automation layer

- Decide where the scripts, systemd templates, and configuration are maintained.
- The actual publication repository is `web/`; the parent project currently is
  not a Git worktree, so the automation must either be versioned separately or
  deliberately maintained outside the site repository.

### Blocker 5: Define the lock boundary for ad-hoc commands

- Either wrap direct export and maintenance writers in `ProcessLock`, or clearly
  document them as exclusive maintenance operations that must not overlap with
  scheduled jobs.
- Keep the same absolute lock path for every cooperating process in a deployment.

## Implementation phases

### Phase 1: Configuration and launcher contract

**Description:** Define and validate the one-command interface for starting a
window, for example `start-cart-window --start-date YYYY-MM-DD --year YYYY
--semester fall`.

**Acceptance criteria:**

- [x] Invalid dates, missing semester/year, unsupported timezone, and unsafe unit
      names are rejected before any systemd change.
- [x] The launcher computes the next calendar day correctly across month, year,
      and leap-year boundaries.
- [x] A dry-run prints the exact collector and cleanup schedules without writing
      units or starting jobs.

**Verification:**

- [x] Unit tests cover ordinary, month-boundary, year-boundary, and leap-year
      start dates.
- [x] `systemd-analyze calendar` validates the generated calendar expressions.

**Dependencies:** Blockers 1–5.

**Likely files:** launcher script, configuration module, tests, documentation.

### Phase 2: Reusable systemd templates

**Description:** Add collector and cleanup service/timer templates that accept a
date-specific instance and invoke the existing locked worker.

**Acceptance criteria:**

- [x] Collector instances run cart-only collection with the selected year and
      semester.
- [x] Collector schedule includes 09:00 and 16:00, but no 16:10 run.
- [x] Cleanup runs after the final trigger, stops future collector activations,
      waits for active work, and never kills an active collector.
- [x] Cleanup verifies the collector's final systemd `Result=success` and
      actual execution timestamp before deleting generated window state.
- [ ] Cleanup provides an automatic retry or explicit failed-sample recovery
      policy; failed validation currently leaves state for manual recovery.
- [x] Templates are reusable; cleanup does not remove them.

**Verification:**

- [x] Generated service and timer units pass `systemd-analyze verify`.
- [x] A fake delayed collector proves cleanup waits rather than terminating it.
- [x] A lock-contention test proves cleanup waits for the shared lock.

**Dependencies:** Phase 1.

**Likely files:** `systemd/`, cleanup script, launcher script, lock tests.

### Phase 3: Installation and lifecycle management

**Description:** Extend the installer so it installs reusable user units, starts
one requested window, and can inspect or recover an active window.

**Acceptance criteria:**

- [x] Installation is idempotent and does not disturb the existing full-update
      timer.
- [x] Starting a second window is rejected or explicitly handled rather than
      silently replacing the first one.
- [x] The launcher performs `daemon-reload` and reports the exact active unit
      names.
- [x] After cleanup, no date-specific collector or cleanup timer remains enabled.

**Verification:**

- [x] Install twice and confirm the result is unchanged.
- [x] Inspect `systemctl --user list-timers` before and during the installed
      window.
- [x] Confirm the collector journal records a successful bounded service run.
- [ ] Confirm the cleanup journal records completion after the scheduled 16:10
      event.

**Dependencies:** Phase 2.

**Likely files:** installer script, `systemd/README.md`, service/timer templates.

### Phase 4: End-to-end publication and rollout

**Description:** Connect the bounded window to the corrected export/publishing
path and document the future-semester procedure.

**Acceptance criteria:**

- [x] Each successful collection exports trend data atomically.
- [x] Git operations run from `web/`, commit only intended data, and fail the
      service if publication is unexpectedly unavailable.
- [x] Full updates and cart updates serialize through the same lock.
- [x] A future semester can be configured and launched from the documented
      command without editing unrelated scripts.

**Verification:**

- [x] Run a no-network dry run for a future semester.
- [x] Run an end-to-end test with a local/fake backend and `PUBLISH_PUSH=0`.
- [ ] Confirm the live journal shows collection, export, commit decision, and
      cleanup separately after the scheduled cleanup event.

**Dependencies:** Phases 1–3 and all blockers.

## Risks and mitigations

| Risk | Impact | Mitigation |
|---|---:|---|
| Laptop sleeps during the window | Medium | The suspended owner keeps the lock; cart events are not replayed, while persistent full/cleanup timers may catch up. Waiting workers exit 75 after 900 seconds; retry policy is deferred. |
| Collector runs past 16:00 | High | Cleanup stops future triggers, then waits on service state/shared lock with a bounded timeout. |
| Systemd clock coalesces a trigger | Medium | Use explicit `Asia/Seoul` and `AccuracySec=1s`; retain the application-level window guard. |
| Git publication fails | High | Correct the `web/` root and fail closed unless local-only mode is explicit. |
| Cleanup removes a reusable template | High | Generate per-window instances and restrict deletion to those exact instance paths. |
| A non-cooperating process writes concurrently | Medium | Wrap all relevant writers or document an exclusive maintenance gate. |

## Implementation choices made

- Render persistent date-specific timer instance files and keep reusable
  `class-checker.cart@.service` and `class-checker.cart-cleanup@.service`
  templates installed.
- Trigger cleanup at 16:10 so the final 16:00 collector has a full interval to
  finish before cleanup begins its wait.
- Keep cleanup timers persistent for recovery after downtime while collector
  timers remain non-replaying.
- Let the launcher derive the application cart window and supply it through a
  per-window environment file, leaving the canonical `collect.env` unchanged.

## Implementation boundary

This document records the agreed architecture, implementation order, and the
post-implementation audit. The date-bounded collector and cleanup timer are
implemented and installed from the root automation repository; the public
`web/` repository must remain untouched except when the publisher deliberately
updates deployment data.

## Post-implementation audit (2026-08-04)

The current behavior is intentionally split between a normal full refresh and a
date-bounded cart worker:

- The crawler defaults, `refresh.sh`, Makefile targets, scheduled update
  wrapper, and admin refresh endpoint use `catalog,enrollment,grading`.
- The bounded worker alone selects `cart`, runs every ten minutes from 09:00 on
  the start date through 16:00 on the next date, and schedules cleanup at
  16:10. The legacy broad count timer must remain disabled.
- All cooperating database/export/publication writers use the same advisory
  `data/.crawl.lock`. The file remains on disk; the kernel lock on its open
  descriptor is released on normal return, nonzero result, exception, or
  process termination. Systemd's `KillMode=control-group` covers the wrapper
  and its child processes together.
- A suspended owner keeps the lock. A waiter exits 75 after the 900-second
  lock timeout; cleanup waits up to 1800 seconds. Cart timers do not replay
  missed events, while persistent full/cleanup timers may catch up. Separate
  services serialize through the lock rather than creating a durable queue.
- Frequent cart/trend workers export and commit their data in `web/` without
  pushing. The hourly `class-checker.update.timer` full update is the only
  scheduled push boundary, publishing the accumulated local commits once per
  hour. Counts/trend publication enforces commit-only behavior even if
  `PUBLISH_PUSH=1` is inherited.
- The 00:30 and 00:40 bounded runs failed with Chromium profile I/O errors;
  the 00:50 run recovered before the service-template mitigation. After
  installing commit `a59e116`, the 01:00 run completed with systemd
  `Result=success`, processed all 8,625 classes, exported 96 samples, and
  retained web commit `bcdfd02` locally for the hourly push boundary.

The following are deliberately deferred, not silently considered solved:

1. Lock-wait overrun retry, backoff, and durable missed-run handling.
2. Recovery and retry policy after sleep/wake or a service timeout, including
   the cart service's 20-minute `TimeoutStartSec`.
3. Explicit process-group hardening for callers that signal only a wrapper
   outside systemd.
4. Forced or ended-term count modes' legacy cart behavior.
5. Transactional catalog rebuilds after `refresh_all()` clears and commits.
6. Isolation of unrelated pre-staged changes by the Git publisher.
7. Automatic retry or durable recovery of a failed or never-run final sample;
   the `Result=success` and actual-execution gate is implemented.

The final 16:00 cart-worker journal and 16:10 cleanup journal still need to be
checked after the real August 4–5 window. The blocker audit note remains the
detailed historical working record and is intentionally not revised by this
update; this section is the permanent summary of the decision and audit state.
