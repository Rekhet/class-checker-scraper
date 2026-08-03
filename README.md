# Class Checker automation

This repository owns the private crawler, collection configuration, worker
scripts, systemd units, tests, and operational documentation for Class Checker.

The `web/` directory is intentionally excluded from this repository. It is an
independent public Git repository used for GitHub Pages deployment and contains
only the deployable site and generated public data. The publisher operates on
that separate worktree explicitly.

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

## Verification

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_*.py'
systemd-analyze verify systemd/class-checker.update-counts.service \
  systemd/class-checker.update-counts.timer
```
