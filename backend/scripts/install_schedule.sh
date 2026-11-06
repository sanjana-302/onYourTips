#!/usr/bin/env bash
# Install (or remove) a macOS LaunchAgent that runs the daily refresh (`oyt nightly`):
# ingest the last 24 hours → extract full text → retry failures → retag → alerts (ADR 0005).
#
#   ./scripts/install_schedule.sh                 # default: once a day at 01:00 local time
#   ./scripts/install_schedule.sh 1 13            # custom hours (e.g. 01:00 and 13:00)
#   ./scripts/install_schedule.sh --uninstall
#   ./scripts/install_schedule.sh --run-now       # trigger a run immediately (to test)
#
# If the Mac is asleep at a scheduled time, launchd runs the missed job once on wake.
# Logs: data/logs/nightly.log

set -euo pipefail

LABEL="com.onyourtips.ingest"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$PROJECT_DIR/data/logs"
DOMAIN="gui/$(id -u)"

if [[ "$(uname)" != "Darwin" ]]; then
  echo "This script is for macOS (launchd). On Linux, add a cron line instead:" >&2
  echo "  0 1 * * * cd '$PROJECT_DIR' && uv run oyt nightly >> '$LOG_DIR/nightly.log' 2>&1" >&2
  exit 1
fi

case "${1:-}" in
  --uninstall)
    launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
    rm -f "$PLIST"
    echo "Removed $LABEL"
    exit 0 ;;
  --run-now)
    launchctl kickstart -p "$DOMAIN/$LABEL"
    echo "Triggered. Follow with: tail -f '$LOG_DIR/nightly.log'"
    exit 0 ;;
esac

UV="$(command -v uv || true)"
if [[ -z "$UV" ]]; then
  echo "uv not found on PATH. Install it: https://docs.astral.sh/uv/" >&2
  exit 1
fi

HOURS=("$@")
[[ ${#HOURS[@]} -eq 0 ]] && HOURS=(1)

mkdir -p "$LOG_DIR" "$(dirname "$PLIST")"

INTERVALS=""
for h in "${HOURS[@]}"; do
  [[ "$h" =~ ^([0-9]|1[0-9]|2[0-3])$ ]] || { echo "invalid hour: $h" >&2; exit 1; }
  INTERVALS+="      <dict><key>Hour</key><integer>$h</integer><key>Minute</key><integer>0</integer></dict>
"
done

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$LABEL</string>
    <key>WorkingDirectory</key><string>$PROJECT_DIR</string>
    <key>ProgramArguments</key>
    <array>
      <string>$UV</string><string>run</string><string>--quiet</string>
      <string>oyt</string><string>nightly</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
      <key>PATH</key><string>$(dirname "$UV"):/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin</string>
      <key>COLUMNS</key><string>140</string>
    </dict>
    <key>StartCalendarInterval</key>
    <array>
$INTERVALS    </array>
    <key>StandardOutPath</key><string>$LOG_DIR/nightly.log</string>
    <key>StandardErrorPath</key><string>$LOG_DIR/nightly.log</string>
    <key>ProcessType</key><string>Background</string>
    <key>LowPriorityIO</key><true/>
</dict>
</plist>
EOF

plutil -lint "$PLIST" >/dev/null
launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
launchctl bootstrap "$DOMAIN" "$PLIST"

echo "Installed $LABEL — runs daily at: $(printf '%02d:00 ' "${HOURS[@]}")"
echo "Test now:   ./scripts/install_schedule.sh --run-now"
echo "Logs:       tail -f '$LOG_DIR/nightly.log'"
echo "Remove:     ./scripts/install_schedule.sh --uninstall"
