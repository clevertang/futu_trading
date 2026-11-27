#!/bin/bash
# One margin-watch check, as launchd runs it every 10 minutes.
#
# `ftrade margin-watch` exits at once outside US hours without touching OpenD,
# so running it all day is cheap. It runs under launchd (the login session)
# rather than from an agent's shell because the SMTP password lives in the
# login Keychain, which a sandboxed shell cannot read.
#
# If reports/.margin_watch_test exists, this run sends a test mail instead and
# removes the flag -- see `install_margin_watch.sh test-email`.
set -uo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FTRADE="$REPO_DIR/.venv/bin/ftrade"
FLAG="$REPO_DIR/reports/.margin_watch_test"
cd "$REPO_DIR" || exit 1

if [ -f "$FLAG" ]; then
  rm -f "$FLAG"
  echo "$(date '+%F %T %z') test email"
  exec "$FTRADE" margin-watch --test-email
fi

# Skip quietly when OpenD is down; the daily job already reports that.
/usr/bin/nc -z -G 2 127.0.0.1 11111 2>/dev/null || exit 0
exec "$FTRADE" margin-watch
