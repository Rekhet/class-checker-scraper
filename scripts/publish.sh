#!/usr/bin/env bash
# Export generated data and publish it as one serialized operation.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
WEB_ROOT="$ROOT/web"
PY="${PY:-$ROOT/.venv/bin/python}"
# Keep standalone trend publication on the same configured semester as the
# scheduled worker. Explicit YEAR/SEM values still override this file below.
if [ -f "$ROOT/collect.env" ]; then
  set -a
  . "$ROOT/collect.env"
  set +a
fi
# Keep standalone publishing on the same backend as make export-json.
export DB_BACKEND="${DB_BACKEND:-turso}"
export TURSO_DATABASE_URL="${TURSO_DATABASE_URL:-data/turso.db}"
MODE="${1:-full}"

# Standalone publishing is safe too. update.sh/update-counts.sh already hold the
# lock, and the environment marker prevents a nested acquisition.
if [ "${CLASS_CHECKER_PROCESS_LOCK_HELD:-}" != "1" ]; then
  exec "$PY" -m scraper.process_lock --timeout "${CRAWL_LOCK_TIMEOUT:-900}" -- "$0" "$@"
fi

PUBLISH_GIT="${PUBLISH_GIT:-1}"
if [ "$PUBLISH_GIT" = "0" ]; then
  echo "Git publication disabled (PUBLISH_GIT=0); exporting locally"
  PUBLISH_GIT=0
else
  WEB_GIT_ROOT="$(git -C "$WEB_ROOT" rev-parse --show-toplevel 2>/dev/null || true)"
  if [ "$WEB_GIT_ROOT" != "$WEB_ROOT" ]; then
    echo "error: Git publication requested, but '$WEB_ROOT' is not the expected Git worktree" >&2
    exit 1
  fi
fi

case "$MODE" in
  full)
    "$PY" scraper/export_json.py
    MESSAGE="${PUBLISH_COMMIT_MESSAGE:-chore(data): update}"
    PUSH_ALLOWED=1
    ;;
  counts|trend)
    PUBLISH_YEAR="${YEAR:-${COUNT_YEAR:-}}"
    PUBLISH_SEM="${SEM:-${COUNT_SEM:-}}"
    if [ -z "$PUBLISH_YEAR" ] || [ -z "$PUBLISH_SEM" ]; then
      echo "error: set YEAR/SEM or COUNT_YEAR/COUNT_SEM for trend publication" >&2
      exit 2
    fi
    "$PY" scraper/export_json.py --trend-only \
      --years "$PUBLISH_YEAR" --terms "$PUBLISH_SEM"
    # The class index is the pointer that tells the static UI where the trend
    # file lives. The class catalog itself is intentionally not rewritten by a
    # 10-minute count pass.
    MESSAGE="${PUBLISH_COMMIT_MESSAGE:-chore(data): update trend}"
    # Frequent trend commits are deliberately kept local. The hourly full
    # update is the only scheduled path allowed to push the accumulated queue.
    PUSH_ALLOWED=0
    ;;
  *)
    echo "error: unknown publish mode '$MODE' (expected full or counts)" >&2
    exit 2
    ;;
esac

if [ "$PUBLISH_GIT" = "0" ]; then
  echo "export complete; no Git commit/push performed"
  exit 0
fi

if [ "$MODE" = "full" ]; then
  git -C "$WEB_ROOT" add data/classes/ data/trend/ data/explore-index.json
else
  git -C "$WEB_ROOT" add data/trend/ data/classes/index.json
fi

COMMITTED=0
if git -C "$WEB_ROOT" diff --cached --quiet; then
  echo "no new data update to commit"
else
  git -C "$WEB_ROOT" commit -m "$MESSAGE"
  COMMITTED=1
fi

if [ "$PUSH_ALLOWED" = "0" ]; then
  if [ "$COMMITTED" = "1" ]; then
    echo "trend commit retained locally; the hourly full update will push it"
  else
    echo "trend commits retained locally; the hourly full update will push them"
  fi
  exit 0
fi

if [ "${PUBLISH_PUSH:-1}" = "0" ]; then
  echo "Git push disabled (PUBLISH_PUSH=0); local commits retained"
  exit 0
fi

if ! AHEAD="$(git -C "$WEB_ROOT" rev-list --count '@{upstream}..HEAD' 2>/dev/null)"; then
  echo "error: cannot determine whether '$WEB_ROOT' has commits ahead of its upstream" >&2
  exit 1
fi
case "$AHEAD" in
  ''|*[!0-9]*)
    echo "error: invalid ahead-commit count from '$WEB_ROOT': '$AHEAD'" >&2
    exit 1
    ;;
esac

if [ "$AHEAD" -gt 0 ]; then
  git -C "$WEB_ROOT" push
else
  echo "no local commits to push"
fi
