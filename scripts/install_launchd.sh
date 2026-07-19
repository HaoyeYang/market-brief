#!/usr/bin/env bash
# Render the launchd template for the current user and project directory, then
# install it. No personal path is committed to the repository.
set -euo pipefail

PROJECT=${MARKET_BRIEF_PROJECT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}
LABEL=${MARKET_BRIEF_LABEL:-com.example.market-brief}
TEMPLATE="$PROJECT/launchd/com.example.market-brief.plist.template"
AGENTS_DIR="$HOME/Library/LaunchAgents"
TARGET="$AGENTS_DIR/$LABEL.plist"
DOMAIN="gui/$(id -u)"

[ -f "$TEMPLATE" ] || { echo "template missing: $TEMPLATE" >&2; exit 1; }
mkdir -p "$AGENTS_DIR" "$PROJECT/logs"

RENDERED=$(mktemp "${TMPDIR:-/tmp}/market-brief-plist.XXXXXX")
trap 'rm -f "$RENDERED"' EXIT INT TERM

sed -e "s|__PROJECT_DIR__|$PROJECT|g" \
    -e "s|__HOME__|$HOME|g" \
    -e "s|__LABEL__|$LABEL|g" \
    "$TEMPLATE" >"$RENDERED"

plutil -lint "$RENDERED"
launchctl bootout "$DOMAIN/$LABEL" >/dev/null 2>&1 || true
cp "$RENDERED" "$TARGET"
launchctl bootstrap "$DOMAIN" "$TARGET"
launchctl enable "$DOMAIN/$LABEL"
echo "installed $LABEL; local calendar gate checks every five minutes"
