#!/usr/bin/env bash
# Full catalog refresh + static data publication.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"

# Keep the scheduled full refresh on the same semester configuration as the
# counts worker. Explicit UPDATE_YEAR/UPDATE_SEM values still override the
# canonical collect.env values for an intentional one-off run.
if [ -f "$ROOT/collect.env" ]; then
  set -a
  . "$ROOT/collect.env"
  set +a
fi
UPDATE_YEAR="${UPDATE_YEAR:-${COUNT_YEAR:-}}"
UPDATE_SEM="${UPDATE_SEM:-${COUNT_SEM:-}}"
UPDATE_COLLECTIONS="${UPDATE_COLLECTIONS:-catalog,enrollment,grading}"
UPDATE_COLLECTIONS="${UPDATE_COLLECTIONS//[[:space:]]/}"
UPDATE_COLLECTIONS="${UPDATE_COLLECTIONS,,}"
if [ -z "$UPDATE_YEAR" ] || [ -z "$UPDATE_SEM" ]; then
  echo "error: set UPDATE_YEAR/UPDATE_SEM or COUNT_YEAR/COUNT_SEM in collect.env" >&2
  exit 2
fi
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

if [ "${CLASS_CHECKER_PROCESS_LOCK_HELD:-}" != "1" ]; then
  exec "$PY" -m scraper.process_lock --timeout "${CRAWL_LOCK_TIMEOUT:-900}" -- "$0" "$@"
fi

make refresh YEAR="$UPDATE_YEAR" SEM="$UPDATE_SEM" COLLECT="$UPDATE_COLLECTIONS"

# Merge cloud-collected count samples (GitHub Actions collector) into the local
# catalog before export. Scoped to a subshell so the remote TURSO_* credentials
# never leak into the local refresh above or the publish below. Non-fatal: a
# network hiccup should not block publication of the local data.
if [ -f "$ROOT/turso-remote.env" ]; then
  (
    set -a
    . "$ROOT/turso-remote.env"
    set +a
    "$PY" "$ROOT/scraper/pull_counts.py"
  ) || echo "warn: cloud counts pull failed; publishing local data only" >&2
fi
PUBLISH_COMMIT_MESSAGE="${PUBLISH_COMMIT_MESSAGE:-chore(data): update}" \
  "$ROOT/scripts/publish.sh" full
