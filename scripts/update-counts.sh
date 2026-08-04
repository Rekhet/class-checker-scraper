#!/usr/bin/env bash
# Fast, window-aware counts/trend update for the user systemd timer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PY:-$ROOT/.venv/bin/python}"
# collect.env is the canonical scope/window configuration. Its ${VAR:-default}
# assignments preserve explicit launcher overrides.
if [ -f "$ROOT/collect.env" ]; then
  set -a
  . "$ROOT/collect.env"
  set +a
fi
# Explicit DB_BACKEND/TURSO_* values still win for SQLite, an embedded replica,
# or a remote Turso database.
export DB_BACKEND="${DB_BACKEND:-turso}"
export TURSO_DATABASE_URL="${TURSO_DATABASE_URL:-data/turso.db}"

if [ "${CLASS_CHECKER_PROCESS_LOCK_HELD:-}" != "1" ]; then
  exec "$PY" -m scraper.process_lock --timeout "${CRAWL_LOCK_TIMEOUT:-900}" -- "$0" "$@"
fi

COUNT_YEAR="${COUNT_YEAR:-${YEAR:-}}"
COUNT_SEM="${COUNT_SEM:-${SEM:-}}"
COUNT_MODE="${COUNT_MODE:-cart}"
COUNT_COLLECTIONS="${COUNT_COLLECTIONS:-}"

if [ -z "$COUNT_YEAR" ] || [ -z "$COUNT_SEM" ]; then
  echo "error: set COUNT_YEAR and COUNT_SEM in collect.env or the environment" >&2
  exit 2
fi

if [ -n "$COUNT_COLLECTIONS" ]; then
  COLLECTIONS="$COUNT_COLLECTIONS"
else
  case "$COUNT_MODE" in
    cart)
      COLLECTIONS=cart
      ;;
    enrollment)
      COLLECTIONS=enrollment
      ;;
    counts)
      COLLECTIONS=cart,enrollment
      ;;
    *)
      echo "error: COUNT_MODE must be cart, enrollment, or counts" >&2
      exit 2
      ;;
  esac
fi

if [ "${DRY_RUN:-}" = "1" ]; then
  echo "$ROOT/refresh.sh --year $COUNT_YEAR --collect $COLLECTIONS --windowed $COUNT_SEM"
  exit 0
fi

"$ROOT/refresh.sh" --year "$COUNT_YEAR" --collect "$COLLECTIONS" --windowed \
  "$COUNT_SEM"

YEAR="$COUNT_YEAR" SEM="$COUNT_SEM" PUBLISH_PUSH=0 \
  PUBLISH_COMMIT_MESSAGE="${PUBLISH_COMMIT_MESSAGE:-chore(data): update trend}" \
  "$ROOT/scripts/publish.sh" counts
