#!/usr/bin/env bash
# Launch the class-checker server against the LOCAL or REMOTE (Turso cloud) DB.
#
# Usage: ./serve.sh [PORT] [--local | --remote | --static | --db local|remote|static]
#   PORT      optional (default 8011; or env PORT=)  e.g. ./serve.sh 9000
#   --local   local libSQL file data/turso.db (default; honours DB_BACKEND= env)
#   --remote  production Turso cloud — sources ./prod.env (URL + auth token)
#   --static  NO database — serve index.html + web/data/*.json exactly like the
#             production static host (GitHub Pages). /api returns 404. (make serve-prod)
#
# Examples
#   ./serve.sh                 # local DB on 8011
#   ./serve.sh 9000 --remote   # production DB on 9000
#   ./serve.sh --static        # DB-less static preview of the prod page
#   DB_BACKEND=sqlite ./serve.sh   # local, plain SQLite (data/classes.db)
#
set -euo pipefail
cd "$(dirname "$0")"

# Keep manual/admin crawls on the same collection-window and timezone configuration
# as the scheduled workers. collect.env uses ${VAR:-default} assignments so explicit
# environment overrides remain possible.
if [ -f collect.env ]; then
  set -a
  . ./collect.env
  set +a
fi

PORT="${PORT:-8011}"
PY="${PY:-.venv/bin/python}"
DB_MODE="${DB_MODE:-local}"
PROD_ENV="${PROD_ENV:-prod.env}"

usage() { sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    -p|--port)  PORT="${2:-}"; shift 2;;
    --db)       DB_MODE="${2:-}"; shift 2;;
    --local)    DB_MODE="local"; shift;;
    --remote)   DB_MODE="remote"; shift;;
    --static)   DB_MODE="static"; shift;;
    -h|--help)  usage; exit 0;;
    *)          PORT="$1"; shift;;
  esac
done

case "$DB_MODE" in
  local)
    export DB_BACKEND="${DB_BACKEND:-turso}"
    export TURSO_DATABASE_URL="${TURSO_DATABASE_URL:-data/turso.db}"
    ;;
  remote)
    if [ ! -f "$PROD_ENV" ]; then
      echo "error: --remote needs '$PROD_ENV' (run the Turso DB setup first)." >&2
      exit 4
    fi
    # shellcheck disable=SC1090
    . "$PROD_ENV"               # exports DB_BACKEND, TURSO_DATABASE_URL, TURSO_AUTH_TOKEN
    : "${TURSO_DATABASE_URL:?$PROD_ENV is missing TURSO_DATABASE_URL}"
    ;;
  static)
    # No DB at all: the prod page is 100% static (web/data/*.json). server.py
    # skips schema init and answers /api with 404, mirroring the GitHub Pages host.
    export SERVE_STATIC=1
    export WEB_INDEX="${WEB_INDEX:-index.html}"
    ;;
  *) echo "error: --db must be 'local', 'remote', or 'static' (got '$DB_MODE')." >&2; exit 2;;
esac

case "$PORT" in
  ''|*[!0-9]*) echo "error: port must be a number: '${PORT}'" >&2; exit 2;;
esac

if [ ! -x "$PY" ]; then
  echo "error: python not found at '$PY' (run the venv setup first)." >&2
  exit 3
fi

if [ "$DB_MODE" = "static" ]; then
  echo "serving http://127.0.0.1:${PORT}  (static prod preview · no DB · web/data/*.json)" >&2
else
  echo "serving http://127.0.0.1:${PORT}  (DB=${DB_MODE} · ${DB_BACKEND}:${TURSO_DATABASE_URL})" >&2
fi
if [ "${DRY_RUN:-}" = "1" ]; then
  echo "PORT=$PORT $PY scraper/server.py"
  exit 0
fi
exec env PORT="$PORT" "$PY" scraper/server.py
