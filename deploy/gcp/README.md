# Google Cloud deployment notes

## Account preflight

Sign in to the Google Cloud Console with the intended Google account and open
the Billing Overview.  It must show one of: an active Free Trial with remaining
credit/days, or an active Paid billing account.  Also check whether this account
has already used its one-time trial; a normal Google login by itself does not
grant an always-on server.

Create a budget alert before creating the VM.  A budget is an alert, not a hard
spending cap, so also avoid selecting GPU, premium OS, or unnecessary services.

The CLI preflight is:

```bash
gcloud auth list
gcloud projects list
gcloud billing accounts list
gcloud billing projects describe YOUR_GCP_PROJECT_ID
gcloud config set project YOUR_GCP_PROJECT_ID
```

Seeing an account under `gcloud auth list` proves login only. A project is not
deployment-ready until `billingEnabled` is `true`, `billingAccountName` is not
empty, and a default project is set. If `billing accounts list` is empty while
the Billing Console shows an account, the active identity normally lacks Billing
Account Viewer/User IAM on that billing account (or the console is using a
different Google identity). Resolve that in Billing IAM before enabling Compute
Engine or creating a VM.

## Recommended trial VM

For a 90-day proof of concept, use:

- Debian 12 or Ubuntu 24.04 LTS;
- `e2-medium` (4 GB RAM) at minimum, preferably in `us-central1`;
- 30 GB `pd-standard` persistent boot disk;
- no HTTP/HTTPS firewall rules; SSH only;
- automatic restart enabled and deletion protection enabled;
- daily disk snapshot policy in addition to the project's SQLite backup.

The permanently-free Compute Engine allowance is only an `e2-micro`; it has
1 GB RAM and does not satisfy Claude Code's 4 GB minimum.  Do not try to make
this workflow production-stable on that instance merely by adding swap.

The $300 trial can fund the VM while credit remains, but it lasts only 90 days.
It also cannot pay for generative-AI partner models offered as managed APIs, so
using Claude through Vertex AI must be budgeted separately and checked against
the current Vertex/Anthropic terms.  A Claude.ai/Console login or Anthropic API
key is a separate authentication and billing route.

## Migration

After creating the VM, follow [`../server/README.md`](../server/README.md).  The
project does not require GitHub.  Keep `/opt/market-brief`, `/home/marketbrief`,
and `/var/backups/market-brief` on persistent storage.  Pull `out/` back to the
Mac with `rsync`, or configure the included Telegram notification variables.

Before leaving it unattended, verify all four states:

```bash
systemctl is-enabled market-brief.timer market-brief-backup.timer
systemctl list-timers 'market-brief*'
sudo -u marketbrief -H /home/marketbrief/.local/bin/claude --version
sudo journalctl -u market-brief.service -n 200 --no-pager
```

To update provider credentials on the deployed instance from a workstation
without printing their values:

```bash
export GCP_PROJECT_ID=YOUR_GCP_PROJECT_ID
export GCP_ZONE=YOUR_GCP_ZONE            # defaults to us-central1-a
export GCP_INSTANCE=YOUR_INSTANCE_NAME   # defaults to market-brief
./deploy/gcp/sync_credentials.sh
```

`GCP_PROJECT_ID` is mandatory and has no default. Never place API key values in
Git, in a systemd unit, or in a launchd plist.

The helper requires local mode `600`, uploads through IAP, installs the file as
root-owned `/etc/market-brief.env`, removes the temporary copy, and prints only
the key names. A later timer invocation reads the new values automatically.
