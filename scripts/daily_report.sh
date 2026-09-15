#!/bin/bash
# Daily sync + report, meant to be driven by cron/launchd -- not run by hand.
#
# cron runs with almost no environment (no PATH beyond /usr/bin:/bin, no shell
# profile sourced), so every path here is absolute and resolved from the
# script's own location rather than assumed. Relocatable: works from wherever
# this repo is checked out, not hardcoded to one machine.
#
# Requires the Futu OpenD gateway already running and logged in -- this script
# does not start it. If OpenD is unreachable the run is skipped and says so;
# nothing here papers over a failure.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FTRADE="$REPO_DIR/.venv/bin/ftrade"
LOG_DIR="$REPO_DIR/reports"
LOG_FILE="$LOG_DIR/daily.log"
LOCK_DIR="$LOG_DIR/.daily.lock"
JSON_FILE="$LOG_DIR/daily-$(date +%Y-%m-%d).json"
MAX_LOG_BYTES=$((5 * 1024 * 1024))

mkdir -p "$LOG_DIR" || exit 1

# A full sync can run long -- the initial cash-flow backfill took hours -- and
# two of them writing the same SQLite file is how this database got corrupted
# once already. WAL mode makes concurrent *readers* safe, not concurrent
# writers, so an overrunning run must block the next one rather than race it.
# mkdir is atomic on POSIX, which `[ -e ]` followed by a write is not.
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  stale_pid="$(cat "$LOCK_DIR/pid" 2>/dev/null || echo "")"
  if [ -n "$stale_pid" ] && kill -0 "$stale_pid" 2>/dev/null; then
    echo "$(date '+%F %T %z') previous run (pid $stale_pid) still going; skipping" >> "$LOG_FILE"
    exit 0
  fi
  # Holder is gone -- a reboot or a kill -9 left the directory behind.
  echo "$(date '+%F %T %z') clearing stale lock from pid ${stale_pid:-unknown}" >> "$LOG_FILE"
  rm -rf "$LOCK_DIR"
  mkdir "$LOCK_DIR" 2>/dev/null || exit 1
fi
echo $$ > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT

# Append-only across years otherwise. One rotation keeps the recent history
# readable without letting the file grow without bound.
if [ -f "$LOG_FILE" ] && [ "$(wc -c < "$LOG_FILE")" -gt "$MAX_LOG_BYTES" ]; then
  mv -f "$LOG_FILE" "$LOG_FILE.1"
fi

{
  echo "===== $(date '+%Y-%m-%d %H:%M:%S %z') ====="

  if ! /usr/bin/nc -z -G 2 127.0.0.1 11111 2>/dev/null; then
    echo "OpenD not reachable on 127.0.0.1:11111 -- is Futu_OpenD.app running and logged in?"
    echo "Skipping sync; nothing to report."
    exit 1
  fi

  "$FTRADE" sync --cash-flow || { echo "sync failed, see above"; exit 1; }
  "$FTRADE" report --json "$JSON_FILE"
} >> "$LOG_FILE" 2>&1
