#!/bin/bash
# Schedule scripts/daily_report.sh for 08:30 on weekdays, under launchd.
#
#   scripts/install_launchd.sh            install (or reinstall) the job
#   scripts/install_launchd.sh run        run it once now, as launchd would
#   scripts/install_launchd.sh status     show the job's state and last exit
#   scripts/install_launchd.sh uninstall  remove it
#
# Why not cron: cron skips a run that falls while the Mac is asleep and never
# makes it up. On this laptop that lost seven of the first twelve weekday runs,
# and each lost run is a permanently missing daily position/equity snapshot
# (fills are recovered by the next sync; snapshots are not). launchd's
# StartCalendarInterval runs a missed job once when the machine wakes. It
# still cannot run while the machine is powered off.
set -euo pipefail

LABEL=com.ftrade.daily
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/${LABEL}.plist"
DOMAIN="gui/$(id -u)"

write_plist() {
  mkdir -p "$(dirname "$PLIST")" "$REPO_DIR/reports"
  local days=""
  for d in 1 2 3 4 5; do
    days+="    <dict><key>Weekday</key><integer>$d</integer><key>Hour</key><integer>8</integer><key>Minute</key><integer>30</integer></dict>
"
  done
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>${REPO_DIR}/scripts/daily_report.sh</string>
  </array>
  <key>StartCalendarInterval</key>
  <array>
${days}  </array>
  <key>RunAtLoad</key><false/>
  <!-- daily_report.sh logs to reports/daily.log itself; this only catches
       failures that happen before it gets that far. -->
  <key>StandardOutPath</key><string>${REPO_DIR}/reports/launchd.log</string>
  <key>StandardErrorPath</key><string>${REPO_DIR}/reports/launchd.log</string>
</dict>
</plist>
EOF
  plutil -lint "$PLIST" >/dev/null
}

case "${1:-install}" in
  install)
    write_plist
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    launchctl bootstrap "$DOMAIN" "$PLIST"
    echo "installed $LABEL -> $PLIST"
    if crontab -l 2>/dev/null | grep -q "scripts/daily_report.sh"; then
      echo "note: a crontab entry for daily_report.sh still exists; remove it (crontab -e)" \
        "so the job does not run twice on mornings the machine is awake." >&2
    fi
    ;;
  run)
    launchctl kickstart -k "$DOMAIN/$LABEL"
    echo "started $LABEL; follow reports/daily.log"
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
