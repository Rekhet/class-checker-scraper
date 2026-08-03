#!/usr/bin/env bash
# Refresh the SNU class-checker catalog for one or more years and semesters.
#
# A semester is REQUIRED — with none specified the script refuses to run (so a
# stray call can never wipe-and-rebuild every term). Use `all` to opt explicitly
# into every semester. Multiple years and semesters are allowed.
#
# Backend + Turso settings are inherited from the environment, e.g.:
#   DB_BACKEND=turso TURSO_DATABASE_URL=data/turso.db ./refresh.sh --year 2026 fall
#
set -euo pipefail
cd "$(dirname "$0")"

# Optional collection-window config: when 장바구니 (cart) and 수강 인원 (enrolled)
# are sampled into the 인원 추이 history. These periods are independent of the
# term's running dates (e.g. 장바구니 happens before the semester). See
# collect.env.example. Unset bounds mean "always".
[ -f collect.env ] && { set -a; . ./collect.env; set +a; }

PY="${PY:-.venv/bin/python}"
EXTRA=()
terms=()
years=()

# seed years from env YEAR (comma or space separated) if provided
if [ -n "${YEAR:-}" ]; then
  IFS=', ' read -r -a _seed <<< "$YEAR"
  years+=("${_seed[@]}")
fi

declare -A CODE=(
  [spring]=U000200001U000300001
  [fall]=U000200002U000300001
  [summer]=U000200001U000300002
  [winter]=U000200002U000300002
)

usage() {
  cat >&2 <<EOF
Usage: $0 [options] <semester...>

  semester   spring | fall | summer | winter | all   (one or more; required)
             aliases: 1 1학기 | 2 2학기 | 여름 여름학기 | 겨울 겨울학기 | 전체
             'all' expands to every semester.

  options
    --collect COMPONENTS  comma-separated catalog,enrollment,cart,grading (or all)
    --year YYYY          year to crawl (REQUIRED; repeatable or comma-list,
                         or env YEAR=; e.g. --year 2025 --year 2026
                         or --year 2025,2026)
    --counts-only        fast pass: live enrolment counts only, no Excel
    --cart-only          counts-only pass: update/sample 장바구니 only
    --windowed           counts-only pass: skip outside collect.env windows
    --no-counts          Excel + timing only, skip the count overlay
    --no-search-timing   skip search-result timing recovery for lagging terms
    --force              forced past-term update: ignore collect.env windows,
                         sample 수강 인원 only (no 장바구니)

Examples
    $0 --year 2026 fall
    $0 --year 2025,2026 spring fall
    $0 --year 2026 all
    DB_BACKEND=turso TURSO_DATABASE_URL=data/turso.db $0 --year 2026 fall
    $0 --year 2026 --counts-only spring fall
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --collect)           EXTRA+=("$1" "$2"); shift 2;;
    --year)            IFS=', ' read -r -a _y <<< "$2"; years+=("${_y[@]}"); shift 2;;
    --counts-only|--cart-only|--windowed|--no-counts|--no-search-timing|--force)
                       EXTRA+=("$1"); shift;;
    -h|--help)         usage; exit 0;;
    all|전체)           terms+=("${CODE[spring]}" "${CODE[fall]}" "${CODE[summer]}" "${CODE[winter]}"); shift;;
    spring|fall|summer|winter)  terms+=("${CODE[$1]}"); shift;;
    1|1학기)            terms+=("${CODE[spring]}"); shift;;
    2|2학기)            terms+=("${CODE[fall]}");   shift;;
    여름|여름학기)       terms+=("${CODE[summer]}"); shift;;
    겨울|겨울학기)       terms+=("${CODE[winter]}"); shift;;
    *) echo "error: unknown semester or option: $1" >&2; usage; exit 2;;
  esac
done

if [ "${#terms[@]}" -eq 0 ]; then
  echo "error: at least one semester is required (refusing to refresh all; use 'all')." >&2
  usage
  exit 1
fi

if [ "${#years[@]}" -eq 0 ]; then
  echo "error: a year is required (--year YYYY or env YEAR=)." >&2
  usage
  exit 1
fi

# de-duplicate (preserve order), comma-join
YEARS=$(printf '%s\n' "${years[@]}" | awk 'NF && !seen[$0]++' | paste -sd, -)
TERMS=$(printf '%s\n' "${terms[@]}" | awk '!seen[$0]++' | paste -sd, -)

if [ ! -x "$PY" ]; then
  echo "error: python not found at '$PY' (run the venv setup first)." >&2
  exit 3
fi

CMD=("$PY" scraper/crawl.py --years "$YEARS" --terms "$TERMS" ${EXTRA[@]+"${EXTRA[@]}"})

if [ "${DRY_RUN:-}" = "1" ]; then
  echo "${CMD[*]}"
  exit 0
fi

# The process lock is shared with the web server and the publishing wrappers.
# A wrapper that already owns it marks the environment so this entry point can
# still be called directly without deadlocking when nested under update.sh.
if [ "${CLASS_CHECKER_PROCESS_LOCK_HELD:-}" != "1" ]; then
  exec "$PY" -m scraper.process_lock --timeout "${CRAWL_LOCK_TIMEOUT:-900}" -- "$0" "$@"
fi
echo "refreshing years=$YEARS terms=$TERMS ${EXTRA[*]:-}" >&2
exec "${CMD[@]}"
