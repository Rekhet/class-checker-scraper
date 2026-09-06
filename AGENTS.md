# Repository working rules

## Keep operational documentation synchronized

After completing an action that changes code behavior, data flow, scheduling,
publication, locking, or other operational state, update every relevant
canonical document before declaring the action complete. Documentation is part
of the same change and should be committed with it.

For automation changes, check at least:

- `systemd/README.md` for units, timers, installation, locking, and publication;
- `docs/maintenance.html` for operator procedures, recovery, and troubleshooting;
- `docs/crawl.html` for collection sources, windows, and metric semantics; and
- the relevant `docs/superpowers/plans/` decision or implementation document for
  status, rationale, deferred work, and verification results.

Record observed dates, unit names, commit IDs, and pending live verification
explicitly. Do not describe a pending check as complete. Read-only audits do
not require documentation changes, but any completed mutation or operational
fix does.

## Remote collection runs from GitHub, not this machine

The 10-minute 인원 (counts) collection does NOT run locally. It runs on
GitHub-hosted runners: cron-job.org fires a `workflow_dispatch` every 10
minutes → `.github/workflows/collect-counts.yml` on
**github.com/Rekhet/class-checker-scraper** (this repository's public remote)
→ `scraper/cloud_collect.py` crawls and pushes `count_samples` to the cloud
Turso database (`turso-remote.env`, untracked, holds the credentials). The
local hourly `scripts/update.sh` merges those samples back via
`scraper/pull_counts.py`, copies the newest sample onto the catalog's volatile
columns via `python -m scraper.sync_counts`, and exports. It does **not** crawl
sugang: the scheduled run is merge + publish only, and `UPDATE_CRAWL=1` is the
opt-in for a catalog/평가방식 refresh that only a local crawl can collect.

Consequences every change must respect:

- **Pushing to `main` IS deploying.** The runners check out `main`; local
  edits do nothing remotely until committed and pushed.
- **Schema changes must migrate BOTH databases** — the local catalog
  (`data/turso.db`, via `db.init_schema`) and the cloud Turso database
  (ALTER it before pushing code that writes the new column).
- **Counts are stored as deltas** (`count_samples` = changes only,
  `count_passes` = the time axis, `count_latest` = the current value per
  class). Writing every class every pass exhausted the cloud plan's write
  quota mid-semester on 2026-09-04 and stopped collection; do not restore
  dense writes, and do not prune old samples — a deleted row is the baseline
  a later delta is relative to. Every `COUNT_KEYFRAME_HOURS` (default 24) a
  pass re-states every class (`count_passes.full=1`); that is the only thing
  that heals a writer/reader baseline split, so do not remove it.
- **Verify a collector change against an actual runner pass** (the next
  cron run's samples in the cloud DB), not only against a local run.
- **Only `count_samples` crosses the cloud boundary.** Catalog facts (new,
  renamed, 폐강대상 classes, professor, room, language, timing, 평가방식) move
  only when someone runs `UPDATE_CRAWL=1 ./scripts/update.sh` or `make
  refresh`; a class missing from the cloud roster is never counted there until
  that roster is re-seeded from the local catalog.
- A collection failure on the runner fails the Actions run loudly by design
  (retries exhausted or an incomplete roster) and emails the owner; do not
  soften that path.

The historical blocker audit at
`docs/superpowers/audits/2026-08-03-class-checker-blockers.md` must remain
unchanged unless the user explicitly requests an update. The `web/` directory
is a separate public deployment repository and must not be changed for private
automation or documentation work unless the user explicitly includes it in
scope.
