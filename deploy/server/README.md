# Linux server deployment

The server needs at least 4 GB RAM, persistent storage, Python 3.10+, `jq`, and
outbound HTTPS. The default server backend uses NVIDIA/Z.AI GLM-5.2 followed by
Kimi K3 and does not require Claude Code. An optional hybrid route keeps the GLM
research tier and replaces only the writer with Claude Opus. The provided units run as the dedicated
`marketbrief` user and never place secrets in the repository.

## 1. Copy from the workstation

Create an Ubuntu/Debian VM, then copy the project without GitHub:

```bash
rsync -az --delete \
  --exclude '.git/' --exclude '.venv*/' \
  --exclude 'out/' --exclude 'data/' --exclude 'logs/' \
  "$HOME/market-brief/" YOUR_USER@YOUR_SERVER:/tmp/market-brief/
```

On the server:

```bash
sudo apt update
sudo apt install -y python3-venv python3-pip jq rsync curl ca-certificates
cd /tmp/market-brief
sudo ./deploy/server/install_systemd.sh
```

On first install the script carries the existing SQLite history database into
`/opt/market-brief/state` if the destination has no database.  It deliberately
does not overwrite an existing server database during later code updates.

## 2. Configure model providers

Put non-secret settings in `/etc/market-brief.env`. Store `MOONSHOT_API_KEY`,
`ZAI_API_KEY`, and optionally `NVIDIA_API_KEY` in the separate root-only
`/etc/market-brief.credentials.env`. NVIDIA is attempted first for GLM-5.2; five transient
failures fall back to paid Z.AI. Authentication/validation errors fall back
immediately because repeating a bad key cannot recover. Keep the file root-only:

```bash
sudoedit /etc/market-brief.env
sudo chmod 600 /etc/market-brief.env
sudoedit /etc/market-brief.credentials.env
sudo chmod 600 /etc/market-brief.credentials.env
```

The systemd unit sets `MARKET_BRIEF_RUNNER=/opt/market-brief/run_portable.sh`.
Keep that runner for the hybrid route; set `MARKET_BRIEF_WRITER=claude` only
after the dedicated service user has authenticated successfully.

### Optional Claude subscription writer

Install Claude Code with Anthropic's current recommended native installer as
the `marketbrief` user, then run `claude` and choose the Claude account with
subscription. Remote SSH login can be completed in a phone or workstation browser using
the URL and code printed by the CLI. Never copy the workstation keychain or OAuth files
to the VM. Verify that `authMethod` is `claude.ai`, then update the root-owned
environment file:

```bash
sudo -iu marketbrief
curl -fsSL https://claude.ai/install.sh | bash -s stable
command -v claude
claude --version
claude doctor
claude auth status --json
exit
sudoedit /etc/market-brief.env
sudo chmod 600 /etc/market-brief.env
```

Add these non-secret settings:

```dotenv
MARKET_BRIEF_WRITER=claude
MARKET_BRIEF_CLAUDE_MODEL=opus
MARKET_BRIEF_CLAUDE_EFFORT=high
CLAUDE_BIN=/home/marketbrief/.local/bin/claude
```

The current `opus` alias resolved to `claude-opus-4-8` during acceptance, and
the exact resolved model is persisted per run. Kimi remains the automatic
fallback if OAuth expires, the subscription limit is reached, or the Claude
payload fails validation. Keep `MOONSHOT_API_KEY` configured for that reason.
Do not set `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` for this route: the
writer deliberately strips both before launching Claude so a subscription run
cannot silently become a Console API charge. Pro/Max usage is shared with the
Claude apps and is best-effort rather than an API SLA; API/Bedrock/Vertex is
still the stronger choice for unattended production guarantees.

To switch the entire pipeline back to the original multi-agent Claude workflow
instead of only replacing the writer, set
`MARKET_BRIEF_RUNNER=/opt/market-brief/run.sh`. That route uses substantially
more Claude subscription capacity than the hybrid route.

## 3. Test and enable

First use a zero-model-cost calendar check, then run one paid acceptance test
inside its intended window (or deliberately use the documented force flag):

```bash
sudo -u marketbrief -H /opt/market-brief/.venv/bin/python \
  /opt/market-brief/market_clock.py --date "$(TZ=America/Chicago date +%F)" --mode preopen
sudo systemctl start market-brief.service
sudo journalctl -u market-brief.service -n 200 --no-pager
sudo systemctl start market-brief.timer market-brief-backup.timer
systemctl list-timers 'market-brief*'
```

The five-minute timer is cheap: `schedule.sh` calls Claude only when the local
NYSE-aware gate says a report is due.  `Persistent=true` resumes timer checks
after a reboot, but a check outside the valid market window still does not
generate a mislabeled report.

## 4. Reports, notifications, sync, and backup

Reports remain in `/opt/market-brief/out`.  To pull them back to the workstation:

```bash
mkdir -p "$HOME/market-brief/out-server"
rsync -az YOUR_USER@YOUR_SERVER:/opt/market-brief/out/ \
  "$HOME/market-brief/out-server/"
```

For push notifications, create a Telegram bot and set `TELEGRAM_BOT_TOKEN` and
`TELEGRAM_CHAT_ID` in `/etc/market-brief.env`.  Without them Linux writes status
to the systemd journal; macOS continues to use Notification Center.

The daily backup timer uses SQLite's online backup API, runs an integrity
check, stores 30 days under `/var/backups/market-brief`, and prunes older files.
Mount that directory on a persistent disk.  A backup on the same VM is not a
disaster-recovery copy: also use provider disk snapshots or copy the backups to
object storage.  Test restoration periodically:

```bash
sudo systemctl start market-brief-backup.service
sudo journalctl -u market-brief-backup.service -n 50 --no-pager
sqlite3 /var/backups/market-brief/market_brief.TIMESTAMP.sqlite3 'PRAGMA integrity_check;'
```

## 5. Private browser viewer

The viewer listens only on the server's loopback interface; it does not open a
public web port. Start it on the server, then keep this IAP/SSH tunnel running
on the workstation:

```bash
sudo systemctl enable --now market-brief-viewer.service
gcloud compute ssh YOUR_INSTANCE_NAME \
  --project=YOUR_GCP_PROJECT_ID --zone=YOUR_GCP_ZONE \
  --tunnel-through-iap \
  --ssh-flag='-o ControlMaster=no' --ssh-flag='-o ControlPersist=no' \
  --ssh-flag='-N' --ssh-flag='-L 8080:127.0.0.1:8080'
```

Open <http://127.0.0.1:8080> locally. Closing the terminal closes the tunnel;
the VM never exposes the viewer to the internet. The page is deliberately
read-only and escapes report text rather than executing embedded HTML.

### Phone access with Cloud Run IAP

`deploy/cloudrun/` documents the private mobile viewer. A successful report
invokes `cloud_publish.py`, which uploads only an allowlisted and sanitized
projection to a private bucket. Cloud Run mounts the bucket read-only and IAP
restricts the browser URL to explicitly authorized Google accounts.

For Gmail completion delivery, set these values without committing them:

```dotenv
GMAIL_SMTP_USER=you@gmail.com
GMAIL_APP_PASSWORD=your_16_digit_google_app_password
MARKET_BRIEF_EMAIL_TO=you@gmail.com
MARKET_BRIEF_WEB_URL=https://your-private-viewer.run.app
```

Use a Google App Password, not the account password. `notify.py` sends a plain
text message with the IAP link and no tracking pixels. Cloud publication or
mail failures are written to `logs/cloud-publish.log` and `logs/notify.log` and
do not invalidate a report that already passed publication gates.

Do not `source` either credentials file in a shell. They are parsed as dotenv
or systemd `EnvironmentFile` data, and unquoted App Password spaces are not
shell-safe. Use `deploy/gcp/sync_credentials.sh` for credential updates and let
the systemd service load both environment files.
