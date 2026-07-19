#!/usr/bin/env bash
# Upload provider credentials from a workstation to a Compute Engine VM as
# root-owned /etc/market-brief.env. Values are never printed; only key names are.
set -Eeuo pipefail

: "${GCP_PROJECT_ID:?Set GCP_PROJECT_ID first}"
PROJECT_ID=$GCP_PROJECT_ID
ZONE=${GCP_ZONE:-us-central1-a}
INSTANCE=${GCP_INSTANCE:-market-brief}
CREDENTIALS=${MARKET_BRIEF_CREDENTIALS:-${HOME:?}/.config/market-brief/credentials.env}
GCLOUD=${GCLOUD_BIN:-$(command -v gcloud || true)}
REMOTE_TMP="/tmp/market-brief-credentials.$$.env"
LOCAL_TMP=""

cleanup() {
  local status=$?
  [ -n "$LOCAL_TMP" ] && rm -f "$LOCAL_TMP"
  if [ -n "${REMOTE_UPLOADED:-}" ]; then
    "$GCLOUD" compute ssh "$INSTANCE" \
      --project="$PROJECT_ID" --zone="$ZONE" --tunnel-through-iap \
      --command="rm -f '$REMOTE_TMP'" >/dev/null 2>&1 || true
  fi
  return $status
}
trap cleanup EXIT INT TERM

[ -n "$GCLOUD" ] && [ -x "$GCLOUD" ] || {
  echo "gcloud not executable: set GCLOUD_BIN or install the Google Cloud CLI" >&2
  exit 1
}
[ -f "$CREDENTIALS" ] || { echo "credentials file missing: $CREDENTIALS" >&2; exit 1; }
PERMS=$(stat -f '%Lp' "$CREDENTIALS" 2>/dev/null || stat -c '%a' "$CREDENTIALS")
[ "$PERMS" = 600 ] || { echo "credentials must have mode 600 (found $PERMS)" >&2; exit 1; }

# Key names only; values are never read into the log or the shell environment.
KEY_NAMES=$(awk -F= 'NF && $1 !~ /^[[:space:]]*#/ {gsub(/[[:space:]]/, "", $1); print $1}' "$CREDENTIALS")
for required in MOONSHOT_API_KEY ZAI_API_KEY; do
  printf '%s\n' "$KEY_NAMES" | grep -qx "$required" || {
    echo "missing required key: $required" >&2
    exit 1
  }
done

LOCAL_TMP=$(mktemp "${TMPDIR:-/tmp}/market-brief-credentials.XXXXXX")
chmod 600 "$LOCAL_TMP"
cat "$CREDENTIALS" >"$LOCAL_TMP"

REMOTE_UPLOADED=1
"$GCLOUD" compute scp "$LOCAL_TMP" "$INSTANCE:$REMOTE_TMP" \
  --project="$PROJECT_ID" --zone="$ZONE" --tunnel-through-iap
"$GCLOUD" compute ssh "$INSTANCE" \
  --project="$PROJECT_ID" --zone="$ZONE" --tunnel-through-iap \
  --command="set -e; chmod 600 '$REMOTE_TMP'; sudo install -o root -g root -m 600 '$REMOTE_TMP' /etc/market-brief.env; rm -f '$REMOTE_TMP'; sudo stat -c '%a %U:%G %n' /etc/market-brief.env; sudo awk -F= 'NF && \$1 !~ /^#/ {print \$1}' /etc/market-brief.env"
REMOTE_UPLOADED=""

if ! printf '%s\n' "$KEY_NAMES" | grep -qx NVIDIA_API_KEY; then
  echo "NVIDIA_API_KEY is not configured; paid Z.AI will be used directly."
fi
