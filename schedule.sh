#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT=${MARKET_BRIEF_PROJECT:-$SCRIPT_DIR}
if [ -z "${HOME:-}" ]; then
  if [ "$(uname -s)" = Darwin ]; then
    HOME=$(/usr/bin/dscl . -read "/Users/$(id -un)" NFSHomeDirectory | /usr/bin/awk '{print $2}')
  else
    HOME=$(getent passwd "$(id -u)" | cut -d: -f6)
  fi
  export HOME
fi
export TZ=${TZ:-America/Chicago}
export PATH="/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:${PATH:-}"

PY=${MARKET_BRIEF_PYTHON:-$PROJECT/.venv/bin/python}
RUNNER=${MARKET_BRIEF_RUNNER:-$PROJECT/run.sh}
D=$(date +%F)
cd "$PROJECT"
[ -x "$RUNNER" ] || { echo "market-brief runner is not executable: $RUNNER" >&2; exit 1; }

# Called by launchd every five minutes.  Calendar checks are local and free;
# run.sh is invoked only inside an NYSE-aware due window.
for MODE in preopen close; do
  if "$PY" market_clock.py --date "$D" --mode "$MODE" >/dev/null 2>&1; then
    PROFILE=standard
    [ "$MODE" = preopen ] && PROFILE=deep
    BRIEF_PROFILE=$PROFILE "$RUNNER" "$MODE" --scheduled
  fi
done
