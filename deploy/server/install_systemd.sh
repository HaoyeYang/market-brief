#!/usr/bin/env bash
set -Eeuo pipefail

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo deploy/server/install_systemd.sh" >&2; exit 1; }

PROJECT=/opt/market-brief
SERVICE_USER=marketbrief
SOURCE=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash "$SERVICE_USER"
fi

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$PROJECT"
install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" \
  "$PROJECT/data" "$PROJECT/out" "$PROJECT/logs" "$PROJECT/state" \
  /var/backups/market-brief
if [ "$SOURCE" != "$PROJECT" ]; then
  command -v rsync >/dev/null || { echo "rsync is required" >&2; exit 1; }
  rsync -a --exclude '.git/' --exclude '.venv*/' --exclude 'out/*' \
    --exclude 'data/*' --exclude 'logs/*' --exclude 'state/*' \
    "$SOURCE/" "$PROJECT/"
  if [ -f "$SOURCE/state/market_brief.sqlite3" ] && [ ! -e "$PROJECT/state/market_brief.sqlite3" ]; then
    install -D -m 0600 "$SOURCE/state/market_brief.sqlite3" "$PROJECT/state/market_brief.sqlite3"
  fi
fi
chown -R "$SERVICE_USER:$SERVICE_USER" "$PROJECT" /var/backups/market-brief

if [ ! -x "$PROJECT/.venv/bin/python" ]; then
  runuser -u "$SERVICE_USER" -- python3 -m venv "$PROJECT/.venv"
fi
runuser -u "$SERVICE_USER" -- "$PROJECT/.venv/bin/pip" install -r "$PROJECT/requirements.txt"

install -m 0644 "$PROJECT/deploy/systemd/market-brief.service" /etc/systemd/system/
install -m 0644 "$PROJECT/deploy/systemd/market-brief.timer" /etc/systemd/system/
install -m 0644 "$PROJECT/deploy/systemd/market-brief-backup.service" /etc/systemd/system/
install -m 0644 "$PROJECT/deploy/systemd/market-brief-backup.timer" /etc/systemd/system/
install -m 0644 "$PROJECT/deploy/systemd/market-brief-viewer.service" /etc/systemd/system/
if [ ! -e /etc/market-brief.env ]; then
  install -m 0600 "$PROJECT/deploy/server/market-brief.env.example" /etc/market-brief.env
fi
# Provider keys live in a separate root-only file so that rotating them with
# deploy/gcp/sync_credentials.sh never overwrites operator configuration.
if [ ! -e /etc/market-brief.credentials.env ]; then
  install -m 0600 /dev/null /etc/market-brief.credentials.env
fi

systemctl daemon-reload
systemctl enable market-brief.timer market-brief-backup.timer market-brief-viewer.service

cat <<EOF
Installed but not started. Next:
  1. Edit /etc/market-brief.credentials.env; add MOONSHOT_API_KEY and ZAI_API_KEY (plus NVIDIA_API_KEY when available).
  2. Edit /etc/market-brief.env for non-secret settings (timezone, runner, viewer bucket/URL).
  3. Keep both files mode 600; systemd injects them before dropping privileges.
  4. sudo -u $SERVICE_USER -H $PROJECT/.venv/bin/python $PROJECT/market_clock.py --date YYYY-MM-DD --mode preopen
  5. systemctl start market-brief.timer market-brief-backup.timer market-brief-viewer.service
EOF
