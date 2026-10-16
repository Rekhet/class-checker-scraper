# Class Checker: blockers identified before the next implementation pass

Date: 2026-08-03

These were the blockers from the readiness audit. The progress section below
records what has been addressed. This durable audit note was moved from
`/tmp/class-checker-blockers.md` into the private repository on 2026-08-05;
it remains a historical working record rather than the current implementation
status. The implementation plan and maintenance documentation contain the
current status.

1. Git publication uses the wrong repository root.
   `web/` is the actual Git worktree, but `scripts/publish.sh` checks Git and
   stages paths from the parent project directory. The live count service
   therefore exports locally and exits successfully without committing or
   pushing.

2. Semester and collection settings are not centralized.
   The systemd service and scripts default to 2026/fall/cart, while the actual
   collection dates live separately in `collect.env`. A future semester requires
   coordinated manual edits and a systemd reload.

3. Collection-window documentation has drifted.
   Runtime `collect.env` contains only the August 4–5 cart window, while
   `docs/crawl.html` still documents July 28 as a cart day. The example file also
   contains different hard-coded dates.

4. The automation layer was intentionally kept outside the `web/` Git
   repository. It is now reproducibly versioned in the separate private root
   automation repository; `web/` remains deployment-only and public.

5. Not every ad-hoc writer uses the shared process lock.
   Scheduled refresh/count/publish paths use the lock, but direct export and
   some maintenance commands can still run without it.

6. The current timer was bounded by a fixed weekly schedule.
   It runs every ten minutes only on weekdays from 09:10 through 20:50. It does
   not yet accept a semester-specific start date and automatically stop at a
   two-day cart window's exact end time.

## Progress after the first remediation pass

1. Resolved in `scripts/publish.sh`: Git operations now use the actual
   `/home/toxiclemon/project/class-checker/web` worktree and fail closed when
   Git publication is requested without that worktree.
2. Resolved for scheduled and documented launch paths: `collect.env` is the
   canonical source for year, semester, mode, timezone, and collection windows.
   Explicit environment overrides remain available.
3. Resolved: active configuration and crawl documentation use the August 4–5
   cart window; July 28 is marked as historical and not collected.
4. Resolved by the root private automation repository. `web/` remains a
   separate public deployment repository and is ignored by the root repo.
5. Resolved for direct export and maintenance writers: they now use the same
   absolute `data/.crawl.lock` and return the lock-busy exit code on contention.
6. Resolved: `scripts/start-cart-window` now renders and activates a
   date-specific user collector timer plus a persistent 16:10 cleanup timer.
   The August 4–5, 2026 window is installed. The obsolete broad count timer is
   disabled so it cannot duplicate cart requests; the full-update timer remains
   enabled and active.

## Deferred after the final refresh and lock audit

Date: 2026-08-03

The following items are intentionally not changed in this pass. They are
recorded for a future hardening pass rather than treated as solved.

1. Lock contention and a run exceeding the lock wait.
   `data/.crawl.lock` is an advisory `flock(2)` held by an open file
   descriptor. The file itself remains on disk after release; the kernel lock,
   not the file's presence, protects the database, generated export, and Git
   publication. Successful completion, a nonzero child result, a Python
   exception, and normal interruption through the lock context all release
   the descriptor. If the process is killed, the operating system closes its
   descriptor and releases the lock as well. The focused tests cover failed,
   exceptional, and process-group-interrupted runs.

   A waiting worker currently waits up to `CRAWL_LOCK_TIMEOUT` (900 seconds by
   default), then exits with `75` (`EX_TEMPFAIL`). The lock-wait-overrun case,
   including retry/backoff or a durable missed-run record, is intentionally
   deferred. There is no automatic retry after that timeout.

2. Sleep and wake while a writer owns the lock.
   Suspending the computer does not normally terminate the process, so a
   suspended writer keeps its descriptor and the lock. A second worker waits
   while the first is suspended; if the wait reaches 900 seconds it exits 75.
   The cleanup worker waits separately for up to 1800 seconds and otherwise
   leaves the generated window units and environment in place. The cart timer
   uses `Persistent=false`, so missed cart timer events are not replayed after
   wake. The full-update and cleanup timers are persistent and may receive a
   catch-up activation after wake, subject to their service and lock behavior.
   Recovery and retry semantics after a long sleep are deferred.

3. Overlapping run requests.
   A second activation of the same oneshot systemd service is coalesced while
   that service is active. Different services, such as the full update and
   bounded cart collector, can be activated independently, but the shared
   flock serializes their writers. The later worker waits and then exits 75 if
   the first one does not finish within the wait period. The system does not
   queue an additional durable run. This is the same intentionally deferred
   lock-contention problem above.

4. Process-group hardening for direct interruption.
   The user systemd services use systemd's default control-group termination,
   which is expected to terminate the wrapper and its child together. If a
   caller sends a signal only to the shell/Python wrapper outside systemd, an
   orphaned child could theoretically continue writing after the wrapper's
   descriptor is closed. The bounded cart service also has a 20-minute
   `TimeoutStartSec`; if systemd reaches it, the control group is terminated
   and the flock is released, but partial work may remain and no automatic
   retry is scheduled. Explicit process-group handling, timeout recovery, and
   an additional integration test for direct caller behavior are deferred.

5. Forced or ended-term count modes still have a legacy cart path.
   The normal full refresh, Makefile refresh, admin refresh endpoint, and
   scheduled update now select catalog, enrollment, and grading explicitly.
   The separate legacy `--counts-only --force` path still permits the crawler's
   forced cart update behavior even though forced sampling suppresses live cart
   collection. Its intended semantics need a dedicated decision before it is
   changed.

6. Catalog rebuilds are not transactional at the database level.
   `refresh_all()` clears terms and commits before rebuilding them. A later
   failure can therefore leave the database partially rebuilt or empty even
   though static publication fails closed. A staging/swap or transaction-level
   recovery design is deferred.

7. Publication staging is broader than the intended file list.
   `scripts/publish.sh` stages the intended generated paths, but an unrelated
   pre-existing staged change in the `web/` worktree could still be included by
   its whole-index commit. The publisher should eventually isolate or reject
   unrelated staged changes before committing.

8. Cleanup success and retry policy are not verified.
   The cleanup service waits for the collector and lock to become idle and then
   removes the generated timer/service/env files. It does not yet inspect the
   collector's final systemd `Result=success`, distinguish a failed final
   sample, or schedule an automatic retry before removing the window. Live
   end-to-end verification of the final 16:00 sample and 16:10 cleanup remains
   pending until that real window occurs.
