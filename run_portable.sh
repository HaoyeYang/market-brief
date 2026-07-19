#!/usr/bin/env bash
set -Eeuo pipefail

# Provider-neutral production runner: deterministic data -> SQLite context ->
# NVIDIA GLM-5.2 (five transient attempts) / paid Z.AI fallback -> Kimi K3.
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT=${MARKET_BRIEF_PROJECT:-$SCRIPT_DIR}
PY=${MARKET_BRIEF_PYTHON:-$PROJECT/.venv/bin/python}
export TZ=${TZ:-America/Chicago}
MODE=${1:-preopen}
SCHEDULED=${2:-}

case "$MODE" in preopen|close|intraday) ;; *) echo "usage: $0 {preopen|close|intraday} [--scheduled]" >&2; exit 2 ;; esac
PROFILE=${BRIEF_PROFILE:-standard}
[ "$MODE" = preopen ] && PROFILE=${BRIEF_PROFILE:-deep}
case "$PROFILE" in standard|deep) ;; *) echo "invalid BRIEF_PROFILE=$PROFILE" >&2; exit 2 ;; esac

cd "$PROJECT"
mkdir -p data out logs state
D=$(date +%F)
BASE="$D.$MODE"
STATE_DB="$PROJECT/state/market_brief.sqlite3"
LOCK="$PROJECT/logs/.portable-run.lock"
STAGE=""

cleanup() {
  local rc=$?
  trap - EXIT
  [ -n "$STAGE" ] && [ -d "$STAGE" ] && rm -rf "$STAGE"
  [ -d "$LOCK" ] && [ -f "$LOCK/pid" ] && [ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LOCK"
  exit "$rc"
}
trap cleanup EXIT

fail() {
  local reason=$1 tmp="out/.$BASE.FAILED.tmp"
  {
    echo "reason: $reason"
    echo "date: $D"
    echo "mode: $MODE"
    echo "profile: $PROFILE"
    echo "backend: portable"
    echo "failed_at_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    [ -n "$STAGE" ] && [ -s "$STAGE/stderr.log" ] && { echo "--- stderr ---"; cat "$STAGE/stderr.log"; }
  } > "$tmp"
  mv "$tmp" "out/$BASE.FAILED.txt"
  echo "market-brief portable FAILED ($BASE): $reason" >&2
  "$PY" notify.py --status FAILED --message "$BASE portable — $reason" >/dev/null 2>&1 || true
  exit 1
}

[ -x "$PY" ] || { echo "venv python missing at $PY" >&2; exit 1; }
if ! mkdir "$LOCK" 2>/dev/null; then
  OLD_PID=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$OLD_PID" ] && ! kill -0 "$OLD_PID" 2>/dev/null; then
    rm -rf "$LOCK" && mkdir "$LOCK"
  else
    echo "market-brief: another portable run is active (pid ${OLD_PID:-unknown})" >&2
    exit 0
  fi
fi
echo $$ > "$LOCK/pid"

if [ "$SCHEDULED" = "--scheduled" ] && { [ -f "out/$BASE.md" ] || [ -f "out/$BASE.FAILED.txt" ]; }; then
  exit 0
fi

STAGE=$(mktemp -d "$PROJECT/out/.portable.$BASE.XXXXXX")
CONTEXT="$STAGE/context.json"
CLOCK_ARGS=(--date "$D" --mode "$MODE")
[ "${MARKET_BRIEF_FORCE:-0}" = 1 ] && CLOCK_ARGS+=(--allow-outside-window)
set +e
"$PY" market_clock.py "${CLOCK_ARGS[@]}" > "$CONTEXT" 2> "$STAGE/stderr.log"
CLOCK_RC=$?
set -e
if [ "$CLOCK_RC" -eq 3 ] && [ "$SCHEDULED" = "--scheduled" ]; then exit 0; fi
[ "$CLOCK_RC" -eq 0 ] || fail "market window rejected this run"

DATA="$STAGE/$BASE.json"
RAW="$STAGE/$BASE.raw.json"
"$PY" fetch_data.py --date "$D" --mode "$MODE" --context "$CONTEXT" \
  --out "$DATA" --raw-out "$RAW" 2>> "$STAGE/stderr.log" \
  || fail "deterministic data fetch or freshness gate failed"
HISTORY="$STAGE/$BASE.history.json"
"$PY" history_engine.py --db "$STATE_DB" context --data "$DATA" --out "$HISTORY" \
  2>> "$STAGE/stderr.log" || fail "historical comparison context failed"

REFERENCE_ARGS=()
if [ -e "out/latest.md" ]; then REFERENCE_ARGS=(--reference-report "out/latest.md"); fi
"$PY" multi_provider_brief.py --date "$D" --mode "$MODE" --profile "$PROFILE" \
  --data "$DATA" --history "$HISTORY" --out-dir "$STAGE" "${REFERENCE_ARGS[@]}" \
  2>> "$STAGE/stderr.log" || fail "GLM/Kimi generation failed"

SHADOW="$D.$MODE.dual-shadow"
REPORT="$STAGE/$SHADOW.md"
RANK="$STAGE/$SHADOW.rank.json"
RESEARCH="$STAGE/$SHADOW.glm-research.json"
USAGE="$STAGE/$SHADOW.usage.json"
"$PY" validate_report.py --report "$REPORT" --rank "$RANK" --data "$DATA" \
  --history "$HISTORY" --mode "$MODE" --profile "$PROFILE" \
  2>> "$STAGE/stderr.log" || fail "deterministic report publication gate failed"

mv "$DATA" "data/$BASE.json"
mv "$RAW" "data/$BASE.raw.json"
mv "$RANK" "out/$BASE.rank.json"
mv "$HISTORY" "out/$BASE.history.json"
mv "$RESEARCH" "out/$BASE.glm-research.json"
mv "$USAGE" "out/$BASE.run.json"
"$PY" history_engine.py --db "$STATE_DB" ingest --data "data/$BASE.json" \
  --rank "out/$BASE.rank.json" --report "$REPORT" \
  --run-json "out/$BASE.run.json" --profile "$PROFILE" 2>> "$STAGE/stderr.log" \
  || fail "SQLite history ingest failed"
"$PY" history_engine.py --db "$STATE_DB" metrics --out "$STAGE/metrics.json" \
  2>> "$STAGE/stderr.log" || fail "historical metrics failed"
mv "$STAGE/metrics.json" "out/metrics.json"
mv "$REPORT" "out/$BASE.md"
rm -f "out/$BASE.FAILED.txt"
ln -sfn "$BASE.md" "out/latest.$MODE.md"
ln -sfn "$BASE.md" "out/latest.md"

CHARS=$(wc -m < "out/$BASE.md" | tr -d ' ')
COST=$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("estimated_cost_usd", "unknown"))' "out/$BASE.run.json")
echo "market-brief portable: wrote out/$BASE.md ($CHARS chars, estimated \$$COST)"
"$PY" notify.py --status SUCCESS --message "$BASE portable — $CHARS chars, estimated \$$COST" >/dev/null 2>&1 || true
