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

# OpenD down during the session means nobody is watching: say so (once a day,
# and only in US hours -- the command checks both).
if ! /usr/bin/nc -z -G 2 127.0.0.1 11111 2>/dev/null; then
  exec "$FTRADE" margin-watch --opend-down
fi

# launchd will not start the next run while one is still going, so a hang
# (the futu SDK retries a failed connect forever) would end the night's
# checks. Cap each run at 5 minutes; macOS has no `timeout`, perl's alarm does.
exec /usr/bin/perl -e 'alarm shift; exec @ARGV' 300 "$FTRADE" margin-watch
