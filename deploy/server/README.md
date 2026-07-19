# Linux server deployment

The server needs at least 4 GB RAM, persistent storage, Python 3.10+, `jq`, and
outbound HTTPS. The default server backend uses NVIDIA/Z.AI GLM-5.2 followed by
Kimi K3 and does not require Claude Code. The provided units run as the dedicated
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

Put `MOONSHOT_API_KEY`, `ZAI_API_KEY`, and optionally `NVIDIA_API_KEY` in
`/etc/market-brief.env`. Never commit real key values to Git; `/etc/market-brief.env`
lives outside the repository by design. NVIDIA is attempted first for GLM-5.2; five transient
failures fall back to paid Z.AI. Authentication/validation errors fall back
immediately because repeating a bad key cannot recover. Keep the file root-only:

```bash
sudoedit /etc/market-brief.env
sudo chmod 600 /etc/market-brief.env
```

The systemd unit sets `MARKET_BRIEF_RUNNER=/opt/market-brief/run_portable.sh`.
To deliberately use Claude instead, override that variable and then follow the
Claude authentication notes below.

### Optional Claude backend

Install Claude Code with Anthropic's current recommended native installer as
the `marketbrief` user.  Then choose one unattended authentication route: an
Anthropic API key, Amazon Bedrock, or Google Vertex AI.  This can differ from
the Mac subscription login and changes billing.  Put only the selected values
in `/etc/market-brief.env`; do not put secrets in this directory or a systemd
unit.  Verify the executable path and update `CLAUDE_BIN` if needed:

```bash
sudo -iu marketbrief
curl -fsSL https://claude.ai/install.sh | bash -s stable
command -v claude
claude --version
claude doctor
exit
sudoedit /etc/market-brief.env
sudo chmod 600 /etc/market-brief.env
```

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
