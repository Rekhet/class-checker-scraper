#!/usr/bin/env bash
# Weekly compressed backup of the local catalog (data/turso.db).
#
# count_samples is the one thing that cannot be rebuilt from SNU: it is the
# enrollment history as it happened. This takes a consistent online snapshot
# (SQLite backup API, safe while the updater writes), checks it, compresses it
# with xz -9e (smallest of zstd -19/-22 and xz measured on this database:
# 1.37 GB -> 61 MB), and keeps the newest $BACKUP_KEEP archives.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="${BACKUP_SRC:-$ROOT/data/turso.db}"
DEST="${BACKUP_DIR:-$HOME/.backup/class-checker}"
KEEP="${BACKUP_KEEP:-3}"

[ -f "$SRC" ] || { echo "error: $SRC not found" >&2; exit 1; }
mkdir -p "$DEST"
chmod 700 "$DEST"
stamp="$(date +%Y%m%d-%H%M%S)"
snap="$DEST/.snapshot-$stamp.db"
out="$DEST/turso-$stamp.db.xz"
trap 'rm -f "$snap" "$out.part"' EXIT

sqlite3 "file:$SRC?mode=ro" ".backup '$snap'"
check="$(sqlite3 "$snap" "PRAGMA quick_check")"
if [ "$check" != "ok" ]; then
  echo "error: snapshot failed quick_check: $check" >&2
  exit 1
fi
samples="$(sqlite3 "$snap" "SELECT COUNT(*) FROM count_samples" 2>/dev/null || echo "?")"

nice -n 19 xz -9e -T0 -c "$snap" >"$out.part"
xz -t "$out.part"
mv "$out.part" "$out"
chmod 600 "$out"
rm -f "$snap"

ls -1t "$DEST"/turso-*.db.xz | tail -n +"$((KEEP + 1))" | xargs -r rm -f --
echo "backup: $out ($(stat -c %s "$out") bytes, $samples samples); kept $(ls -1 "$DEST"/turso-*.db.xz | wc -l)"
