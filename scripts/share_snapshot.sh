#!/bin/bash
# Render one period of the dashboard (share mode) to a PNG, for publishing.
#
#   scripts/share_snapshot.sh START END OUT.png [lang] [base] [port]
#   scripts/share_snapshot.sh 2025-05-12 2025-12-31 reports/xueqiu/2025H2.png
#
# Share mode shows only figures that belong to the period -- nothing about the
# account as it stands today -- so the image can sit under a title like
# "2025 H2 summary" without misleading anyone. Needs `ftrade serve` running.
#
# Uses a throwaway Chrome profile so the person's own browser session is never
# touched, and asks the page for its rendered height first so the image is
# exactly one page long. Slow: expect two to three minutes per image, mostly
# headless Chrome start-up and the report build. It is not hung.
set -euo pipefail

if [ $# -lt 3 ]; then
  sed -n '4,5p' "$0" >&2
  exit 2
fi

START=$1
END=$2
OUT=$3
LANG_=${4:-zh}
BASE=${5:-USD}
PORT=${6:-8791}
WIDTH=1280
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
URL="http://127.0.0.1:${PORT}/?share=1&start=${START}&end=${END}&lang=${LANG_}&base=${BASE}"

[ -x "$CHROME" ] || { echo "Google Chrome not found at $CHROME" >&2; exit 1; }
curl -sf -o /dev/null "http://127.0.0.1:${PORT}/" \
  || { echo "dashboard not reachable on :${PORT} -- start it with: ftrade serve --port ${PORT}" >&2; exit 1; }

PROFILE=$(mktemp -d)
trap 'rm -rf "$PROFILE"' EXIT
headless() {
  "$CHROME" --headless=new --disable-gpu --hide-scrollbars --no-first-run \
    --user-data-dir="$PROFILE" --virtual-time-budget=15000 "$@" 2>/dev/null
}

HEIGHT=$(headless --window-size="${WIDTH},1000" --dump-dom "$URL" \
  | grep -oE 'data-height="[0-9]+"' | grep -oE '[0-9]+' | head -1 || true)
[ -n "$HEIGHT" ] || { echo "page did not finish rendering (no data-height)" >&2; exit 1; }

mkdir -p "$(dirname "$OUT")"
headless --force-device-scale-factor=2 --window-size="${WIDTH},${HEIGHT}" --screenshot="$OUT" "$URL"
[ -s "$OUT" ] || { echo "screenshot was not written" >&2; exit 1; }
echo "$OUT  ${START} -> ${END}  ${WIDTH}x${HEIGHT} @2x"
