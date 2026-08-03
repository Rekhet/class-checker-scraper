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
if [ -z "$UPDATE_YEAR" ] || [ -z "$UPDATE_SEM" ]; then
  echo "error: set UPDATE_YEAR/UPDATE_SEM or COUNT_YEAR/COUNT_SEM in collect.env" >&2
  exit 2
fi

if [ "${CLASS_CHECKER_PROCESS_LOCK_HELD:-}" != "1" ]; then
  exec "$PY" -m scraper.process_lock --timeout "${CRAWL_LOCK_TIMEOUT:-900}" -- "$0" "$@"
fi

make refresh YEAR="$UPDATE_YEAR" SEM="$UPDATE_SEM"
PUBLISH_COMMIT_MESSAGE="${PUBLISH_COMMIT_MESSAGE:-chore(data): update}" \
  "$ROOT/scripts/publish.sh" full
