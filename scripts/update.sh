#!/usr/bin/env bash
# Cloud counts merge + static data publication.
#
# The 10-minute 인원 pass runs on GitHub-hosted runners (cron-job.org ->
# collect-counts.yml -> scraper/cloud_collect.py -> cloud Turso), so the
# scheduled hourly run does NOT crawl sugang from this machine: it merges the
# cloud samples (pull_counts), overlays the newest sample onto the catalog
# rows (sync_counts), and publishes. Set UPDATE_CRAWL=1 for an intentional
# catalog/평가방식 refresh, which only a local crawl can collect.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"

# Keep the scheduled run on the same semester configuration as the counts
# collector. Explicit UPDATE_YEAR/UPDATE_SEM values still override the
# canonical collect.env values for an intentional one-off run.
if [ -f "$ROOT/collect.env" ]; then
  set -a
  . "$ROOT/collect.env"
  set +a
fi
UPDATE_YEAR="${UPDATE_YEAR:-${COUNT_YEAR:-}}"
UPDATE_SEM="${UPDATE_SEM:-${COUNT_SEM:-}}"
UPDATE_CRAWL="${UPDATE_CRAWL:-0}"
UPDATE_COLLECTIONS="${UPDATE_COLLECTIONS:-catalog,enrollment,grading}"
UPDATE_COLLECTIONS="${UPDATE_COLLECTIONS//[[:space:]]/}"
UPDATE_COLLECTIONS="${UPDATE_COLLECTIONS,,}"
if [ -z "$UPDATE_YEAR" ] || [ -z "$UPDATE_SEM" ]; then
  echo "error: set UPDATE_YEAR/UPDATE_SEM or COUNT_YEAR/COUNT_SEM in collect.env" >&2
  exit 2
fi
if [ "$UPDATE_CRAWL" = "1" ]; then
  if [ -z "$UPDATE_COLLECTIONS" ]; then
    echo "error: UPDATE_COLLECTIONS cannot be empty" >&2
    exit 2
  fi
  case ",$UPDATE_COLLECTIONS," in
    *,cart,*|*,all,*)
      echo "error: full update cannot collect cart; use the bounded cart worker" >&2
      exit 2
      ;;
  esac
fi

# Same backend selection as the Makefile and scripts/publish.sh, for the
# sample overlay below.
export DB_BACKEND="${DB_BACKEND:-turso}"
export TURSO_DATABASE_URL="${TURSO_DATABASE_URL:-data/turso.db}"

if [ "${CLASS_CHECKER_PROCESS_LOCK_HELD:-}" != "1" ]; then
  exec "$PY" -m scraper.process_lock --timeout "${CRAWL_LOCK_TIMEOUT:-900}" -- "$0" "$@"
fi

if [ "$UPDATE_CRAWL" = "1" ]; then
  make refresh YEAR="$UPDATE_YEAR" SEM="$UPDATE_SEM" COLLECT="$UPDATE_COLLECTIONS"
fi

# Merge cloud-collected count samples (GitHub Actions collector) into the local
# catalog. Scoped to a subshell so the remote TURSO_* credentials never leak
# into a local crawl above or the publish below. This is the only source of
# fresh data for a scheduled run, so a failure is fatal — the run fails loudly
# instead of republishing yesterday's numbers. A local crawl of its own makes
# the pull optional again.
if [ -f "$ROOT/turso-remote.env" ]; then
  if ! (
    set -a
    . "$ROOT/turso-remote.env"
    set +a
    "$PY" "$ROOT/scraper/pull_counts.py"
  ); then
    if [ "$UPDATE_CRAWL" = "1" ]; then
      echo "warn: cloud counts pull failed; publishing locally crawled data only" >&2
    else
      echo "error: cloud counts pull failed and no local crawl ran; nothing fresh to publish" >&2
      exit 1
    fi
  fi
elif [ "$UPDATE_CRAWL" != "1" ]; then
  echo "error: turso-remote.env is missing; the scheduled update has no counts source" >&2
  exit 2
else
  echo "warn: turso-remote.env is missing; publishing locally crawled data only" >&2
fi

# Copy the newest sample onto the catalog's volatile columns: the static export
# reads them off `classes`, which no longer moves on its own without a crawl.
"$PY" -m scraper.sync_counts --year "$UPDATE_YEAR" --semester "$UPDATE_SEM"

PUBLISH_COMMIT_MESSAGE="${PUBLISH_COMMIT_MESSAGE:-chore(data): update}" \
  "$ROOT/scripts/publish.sh" full
