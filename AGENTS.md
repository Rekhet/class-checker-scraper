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

The temporary `/tmp/class-checker-blockers.md` note is historical working
material and must remain unchanged unless the user explicitly requests an
update. The `web/` directory is a separate public deployment repository and
must not be changed for private automation or documentation work unless the
user explicitly includes it in scope.
