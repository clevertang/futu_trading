#!/bin/bash
# Render the dashboard's share mode to a PNG, for publishing.
#
#   scripts/share_snapshot.sh START END OUT.png [lang] [base] [port]
#   scripts/share_snapshot.sh holdings OUT.png [lang] [base] [port]
#
#   scripts/share_snapshot.sh 2025-05-12 2025-12-31 reports/xueqiu/2025H2.png
#   scripts/share_snapshot.sh holdings reports/xueqiu/holdings.png
#
# A period image shows only figures that belong to the period -- nothing about
# the account as it stands today -- so it can sit under a title like "2025 H2
# summary" without misleading anyone. The holdings image is the opposite:
# today's positions, options and capital as of the latest snapshot, for a
# post's 当前持仓 section. Needs `ftrade serve` running.
#
# Uses a throwaway Chrome profile so the person's own browser session is never
# touched, and asks the page for its rendered height first so the image is
# exactly one page long.
#
# Headless Chrome on this machine is slow and occasionally never exits: the
# same command has finished in about two and a half minutes and, on the next
# run, sat for over eleven. So every Chrome call is bounded and retried rather
# than trusted -- expect a few minutes per image, and a clear failure instead
# of an indefinite wait if it keeps sticking.
set -euo pipefail

CALL_TIMEOUT_S=${CALL_TIMEOUT_S:-240}
ATTEMPTS=${ATTEMPTS:-3}

usage() { sed -n '4,5p' "$0" >&2; exit 2; }

if [ "${1:-}" = "holdings" ]; then
  [ $# -ge 2 ] || usage
  OUT=$2
  LANG_=${3:-zh}
  BASE=${4:-USD}
  PORT=${5:-8791}
  QUERY="view=holdings"
  WHAT="holdings"
else
  [ $# -ge 3 ] || usage
  START=$1
  END=$2
  OUT=$3
  LANG_=${4:-zh}
  BASE=${5:-USD}
  PORT=${6:-8791}
  QUERY="start=${START}&end=${END}"
  WHAT="${START} -> ${END}"
fi
WIDTH=1280
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
URL="http://127.0.0.1:${PORT}/?share=1&${QUERY}&lang=${LANG_}&base=${BASE}"

[ -x "$CHROME" ] || { echo "Google Chrome not found at $CHROME" >&2; exit 1; }
curl -sf -o /dev/null "http://127.0.0.1:${PORT}/" \
  || { echo "dashboard not reachable on :${PORT} -- start it with: ftrade serve --port ${PORT}" >&2; exit 1; }

PROFILE=""
DOM=$(mktemp)
cleanup() {
  [ -n "$PROFILE" ] || return 0
  pkill -9 -f "user-data-dir=$PROFILE" 2>/dev/null || true
  rm -rf "$PROFILE"
  PROFILE=""
}
trap 'cleanup; rm -f "$DOM"' EXIT

# One headless Chrome call, killed if it outlives CALL_TIMEOUT_S. macOS has no
# `timeout`, so a watcher subshell does the killing. Each call gets a fresh
# profile: a killed Chrome can leave its profile locked for the next one.
headless() {
  cleanup
  PROFILE=$(mktemp -d)
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --no-first-run \
    --user-data-dir="$PROFILE" --virtual-time-budget=15000 "$@" 2>/dev/null &
  local pid=$!
  ( sleep "$CALL_TIMEOUT_S"; pkill -9 -f "user-data-dir=$PROFILE" 2>/dev/null ) &
  local watcher=$!
  local rc=0
  wait "$pid" || rc=$?
  kill "$watcher" 2>/dev/null || true
  wait "$watcher" 2>/dev/null || true
  return "$rc"
}

HEIGHT=""
for i in $(seq 1 "$ATTEMPTS"); do
  # To a file, not a pipe: inside $( ) headless would run in a subshell and
  # its profile would escape cleanup.
  headless --window-size="${WIDTH},1000" --dump-dom "$URL" > "$DOM" || true
  HEIGHT=$(grep -oE 'data-height="[0-9]+"' "$DOM" | grep -oE '[0-9]+' | head -1 || true)
  [ -n "$HEIGHT" ] && break
  echo "measuring page height: attempt $i/$ATTEMPTS got nothing, retrying" >&2
done
[ -n "$HEIGHT" ] || { echo "page never finished rendering (no data-height)" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
for i in $(seq 1 "$ATTEMPTS"); do
  headless --force-device-scale-factor=2 --window-size="${WIDTH},${HEIGHT}" \
    --screenshot="$OUT" "$URL" || true
  [ -s "$OUT" ] && break
  echo "screenshot: attempt $i/$ATTEMPTS produced nothing, retrying" >&2
done
[ -s "$OUT" ] || { echo "screenshot was not written" >&2; exit 1; }
echo "$OUT  ${WHAT}  ${WIDTH}x${HEIGHT} @2x"
