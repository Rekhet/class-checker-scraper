#!/usr/bin/env bash
# Flush (empty) the catalog — fully or by semester. DESTRUCTIVE.
#
# A scope is REQUIRED: with none specified the script refuses. Use `all` to wipe
# every term, or one/more semester names to wipe only those. Re-crawl with
# refresh.sh afterwards. Backend is inherited from the environment:
#   DB_BACKEND=turso TURSO_DATABASE_URL=data/turso.db ./flush.sh fall
#
set -euo pipefail
cd "$(dirname "$0")"

YEAR="${YEAR:-}"
PY="${PY:-.venv/bin/python}"
ASSUME_YES=0
WIPE_ALL=0
codes=()

declare -A CODE=(
  [spring]=U000200001U000300001
  [fall]=U000200002U000300001
  [summer]=U000200001U000300002
  [winter]=U000200002U000300002
)

usage() {
  cat >&2 <<EOF
Usage: $0 [options] <scope>

  scope      all                              wipe EVERY term
             spring | fall | summer | winter  wipe only those (one or more)
             aliases: 1 1학기 | 2 2학기 | 여름 여름학기 | 겨울 겨울학기

  options
    --year YYYY   year for scoped wipes (REQUIRED unless 'all'; or env YEAR=)
    -y, --yes     skip the confirmation prompt (for scripts/cron)

Examples
    $0 fall
    $0 spring fall
    $0 all
    DB_BACKEND=turso TURSO_DATABASE_URL=data/turso.db $0 -y all
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --year)       YEAR="$2"; shift 2;;
    -y|--yes)     ASSUME_YES=1; shift;;
    -h|--help)    usage; exit 0;;
    all|--all)    WIPE_ALL=1; shift;;
    spring|fall|summer|winter) codes+=("${CODE[$1]}"); shift;;
    1|1학기)       codes+=("${CODE[spring]}"); shift;;
    2|2학기)       codes+=("${CODE[fall]}");   shift;;
    여름|여름학기)  codes+=("${CODE[summer]}"); shift;;
    겨울|겨울학기)  codes+=("${CODE[winter]}"); shift;;
    *) echo "error: unknown scope or option: $1" >&2; usage; exit 2;;
  esac
done

if [ "$WIPE_ALL" -eq 0 ] && [ "${#codes[@]}" -eq 0 ]; then
  echo "error: a scope is required ('all' or semester names)." >&2
  usage
  exit 1
fi

if [ "$WIPE_ALL" -eq 0 ] && [ -z "$YEAR" ]; then
  echo "error: a year is required for scoped flushes (--year YYYY or env YEAR=)." >&2
  usage
  exit 1
fi

if [ "$WIPE_ALL" -eq 1 ]; then
  target="ALL terms"
else
  TERMS=$(printf '%s\n' "${codes[@]}" | awk '!seen[$0]++' | paste -sd, -)
  target="${YEAR} [${TERMS}]"
fi
backend="${DB_BACKEND:-sqlite}"

if [ "${DRY_RUN:-}" = "1" ]; then
  if [ "$WIPE_ALL" -eq 1 ]; then
    echo "$PY scraper/flush.py --all"
  else
    echo "$PY scraper/flush.py --year $YEAR $(printf '%s ' "${codes[@]}")"
  fi
  exit 0
fi

if [ "$ASSUME_YES" -ne 1 ]; then
  printf 'Flush %s from the %s DB? This is irreversible. [y/N] ' "$target" "$backend" >&2
  read -r ans
  case "$ans" in [yY]|[yY][eE][sS]) ;; *) echo "aborted." >&2; exit 0;; esac
fi

if [ "$WIPE_ALL" -eq 1 ]; then
  exec "$PY" scraper/flush.py --all
else
  IFS=',' read -ra arr <<< "$TERMS"
  exec "$PY" scraper/flush.py --year "$YEAR" "${arr[@]}"
fi
