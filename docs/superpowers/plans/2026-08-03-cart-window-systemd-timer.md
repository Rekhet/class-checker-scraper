# Implementation Plan: Date-Bounded Cart Collection Timer

## Status

Accepted design. The first blocker-remediation pass and the date-bounded timer
implementation are complete in the root automation repository. The live
window is installed; its scheduled cleanup journal entry will be verified after
the final 16:10 event.

Date: 2026-08-03

Related audit note: `/tmp/class-checker-blockers.md`

## Blocker-remediation progress

As of 2026-08-03:

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
3. disables the collector and cleanup timer instances;
4. removes only those generated instances and reloads the user systemd manager.

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
- [x] Cleanup is safe to retry and leaves failed instances available for manual
      recovery.
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
| Laptop sleeps during the window | Medium | Do not replay every missed collector event; run the next future event and use a cleanup recovery path. |
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

This document records the agreed architecture and implementation order. The
date-bounded collector and cleanup timer may now be implemented and installed
from the root automation repository; the public `web/` repository must remain
untouched except when the publisher deliberately updates deployment data.
