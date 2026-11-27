#!/bin/bash
# Run scripts/margin_watch.sh every 10 minutes under launchd.
#
#   scripts/install_margin_watch.sh             install (or reinstall) the job
#   scripts/install_margin_watch.sh test-email  send a test mail through the job
#   scripts/install_margin_watch.sh status      show the job's state and last exit
#   scripts/install_margin_watch.sh uninstall   remove it
#
# The check itself does nothing outside US regular hours, so a fixed interval
# is simpler than a calendar that would have to follow daylight saving time.
set -euo pipefail

LABEL=com.ftrade.marginwatch
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

write_plist() {
  mkdir -p "$(dirname "$PLIST")" "$REPO_DIR/reports"
  cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${REPO_DIR}/scripts/margin_watch.sh</string>
  </array>
  <key>StartInterval</key><integer>600</integer>
  <key>RunAtLoad</key><false/>
  <key>StandardOutPath</key><string>${REPO_DIR}/reports/margin_watch.launchd.log</string>
  <key>StandardErrorPath</key><string>${REPO_DIR}/reports/margin_watch.launchd.log</string>
</dict>
</plist>
PLIST
  plutil -lint "$PLIST" >/dev/null
}

case "${1:-install}" in
  install)
    write_plist
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "installed $LABEL -> $PLIST"
    ;;
  test-email)
    touch "$REPO_DIR/reports/.margin_watch_test"
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "test mail requested; see reports/margin_watch.launchd.log"
    ;;
  status)
    launchctl print "$DOMAIN/$LABEL" | grep -E "state =|last exit code|runs =" || true
    ;;
  uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "removed $LABEL"
    ;;
  *)
    sed -n '4,7p' "$0" >&2
    exit 2
    ;;
esac
