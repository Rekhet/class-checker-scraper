#!/usr/bin/env bash
# Block until the hosts a scheduled update needs are reachable, or give up.
#
# A persistent user timer replays a missed activation the moment the laptop
# resumes, seconds before NetworkManager has a link, so the run used to die on
# the first DNS lookup. Used as ExecStartPre so systemd never starts the update
# on a dead network; failing here leaves the next hourly activation to retry.
#
# Targets default to GitHub (the publish push) and the cloud Turso host read
# from turso-remote.env (the counts pull); pass explicit URLs to override. Only
# the URL is read out of that file — the auth token is never touched.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIMEOUT="${WAIT_ONLINE_TIMEOUT:-300}"
INTERVAL="${WAIT_ONLINE_INTERVAL:-10}"
PROBE_TIMEOUT="${WAIT_ONLINE_PROBE_TIMEOUT:-5}"
# Capped separately: a dead link would otherwise burn the whole budget here
# and leave no time for the reachability probes.
NM_TIMEOUT="${WAIT_ONLINE_NM_TIMEOUT:-60}"

targets=("$@")
if [ "${#targets[@]}" -eq 0 ]; then
  targets=("https://github.com")
  remote_env="$ROOT/turso-remote.env"
  if [ -f "$remote_env" ]; then
    url="$(sed -n 's/^[[:space:]]*TURSO_DATABASE_URL=[[:space:]]*//p' "$remote_env" \
           | tr -d '"'\''' | tail -n 1)"
    host="${url#*://}"
    host="${host%%/*}"
    if [ -n "$host" ]; then
      targets+=("https://$host")
    fi
  fi
fi

# Best effort: NetworkManager knows when the link is actually configured, but a
# machine without it (or with an unmanaged interface) must still fall through to
# the reachability probes below rather than fail here.
if command -v nm-online >/dev/null 2>&1; then
  nm-online -q -t "$NM_TIMEOUT" || true
fi

deadline=$(( $(date +%s) + TIMEOUT ))
while :; do
  unreachable=""
  for target in "${targets[@]}"; do
    # No -f: any HTTP response (401 from the Turso host included) proves
    # reachability; only connection/DNS failures count as offline.
    if ! curl -s -o /dev/null --max-time "$PROBE_TIMEOUT" "$target"; then
      unreachable="$target"
      break
    fi
  done
  if [ -z "$unreachable" ]; then
    exit 0
  fi
  if [ "$(date +%s)" -ge "$deadline" ]; then
    echo "error: still offline after ${TIMEOUT}s (unreachable: $unreachable)" >&2
    exit 1
  fi
  sleep "$INTERVAL"
done
