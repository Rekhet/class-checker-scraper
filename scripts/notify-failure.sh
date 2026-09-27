#!/usr/bin/env bash
# Report a failed class-checker unit (run by class-checker-alert@<unit>.service
# through OnFailure=).
#
# The unit's journal goes to a LOCAL file only (data/logs/failures/, ignored by
# git). The GitHub issue it opens — or comments on, when one is already open
# for the same unit — carries no log text: the repository is public, and a log
# can hold paths, hostnames, or a credential echoed by accident. It says which
# unit failed, when, with what result, and the local file to read.
set -euo pipefail

UNIT="${1:?usage: notify-failure.sh <unit-name>}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="${ALERT_LOG_DIR:-$ROOT/data/logs/failures}"
REPO="${ALERT_REPO:-Rekhet/class-checker-scraper}"
LABEL="ops-alert"
KEEP_LOGS="${ALERT_KEEP_LOGS:-50}"

mkdir -p "$LOG_DIR"
chmod 700 "$LOG_DIR"
stamp="$(date +%Y%m%d-%H%M%S)"
safe_unit="${UNIT//[^A-Za-z0-9@._-]/_}"
log="$LOG_DIR/$safe_unit-$stamp.log"
(
  umask 077
  journalctl --user -u "$UNIT" -n 400 --no-pager >"$log" 2>&1 \
    || echo "journalctl failed with status $?" >>"$log"
)
# keep the newest $KEEP_LOGS failure logs
ls -1t "$LOG_DIR"/*.log 2>/dev/null | tail -n +"$((KEEP_LOGS + 1))" | xargs -r rm -f --

result="$(systemctl --user show -p Result --value "$UNIT" 2>/dev/null || true)"
status="$(systemctl --user show -p ExecMainStatus --value "$UNIT" 2>/dev/null || true)"
when="$(TZ=Asia/Seoul date '+%Y-%m-%d %H:%M:%S KST')"
rel_log="${log#"$ROOT"/}"

title="ops-alert: $UNIT failed"
body="$(cat <<EOF
\`$UNIT\` failed at $when.

- result: \`${result:-unknown}\`, exit status: \`${status:-unknown}\`
- log (local only, not uploaded): \`$rel_log\`

Close this issue once the cause is fixed; the next failure of the same unit
comments here while it stays open.
EOF
)"

gh label create "$LABEL" -R "$REPO" --color B60205 \
  --description "Local automation failure (no logs attached)" >/dev/null 2>&1 || true
# Plain listing, not --search: the search index lags by up to minutes, and two
# failures in quick succession would each open their own issue.
number="$(gh issue list -R "$REPO" --state open --label "$LABEL" -L 100 \
  --json number,title \
  --jq "map(select(.title == \"$title\")) | .[0].number // empty")"
if [ -n "$number" ]; then
  gh issue comment "$number" -R "$REPO" --body "$body" >/dev/null
  echo "commented on issue #$number ($UNIT); log: $rel_log"
else
  gh issue create -R "$REPO" --title "$title" --label "$LABEL" --body "$body"
  echo "opened an issue for $UNIT; log: $rel_log"
fi
