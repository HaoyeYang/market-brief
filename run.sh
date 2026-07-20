#!/usr/bin/env bash
set -Eeuo pipefail

# launchd and systemd both have minimal environments.  Resolve the project
# relative to this script and allow explicit service overrides on Linux.
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
DEFAULT_LANG=C.UTF-8
[ "$(uname -s)" = Darwin ] && DEFAULT_LANG=en_US.UTF-8
export LANG=${LANG:-$DEFAULT_LANG} LC_ALL=${LC_ALL:-${LANG:-$DEFAULT_LANG}}

PY=${MARKET_BRIEF_PYTHON:-$PROJECT/.venv/bin/python}
CLAUDE=${CLAUDE_BIN:-}
if [ -z "$CLAUDE" ]; then
  CLAUDE=$(command -v claude 2>/dev/null || true)
fi
# Fallback for nvm-managed installs that are not on a minimal launchd PATH.
if [ -z "$CLAUDE" ]; then
  for candidate in "${HOME:-}"/.nvm/versions/node/*/bin/claude; do
    [ -x "$candidate" ] && CLAUDE="$candidate"
  done
fi
CLAUDE_PROJECTS_ROOT=${CLAUDE_PROJECTS_DIR:-${HOME:-}/.claude/projects}
STATE_DB="$PROJECT/state/market_brief.sqlite3"
MODE=${1:-preopen}
SCHEDULED=${2:-}

case "$MODE" in
  preopen|close|intraday) ;;
  *) echo "usage: $0 {preopen|close|intraday} [--scheduled]" >&2; exit 2 ;;
esac

if [ -n "${BRIEF_PROFILE:-}" ]; then
  PROFILE=$BRIEF_PROFILE
elif [ "$MODE" = preopen ]; then
  PROFILE=deep
else
  PROFILE=standard
fi
case "$PROFILE" in standard|deep) ;; *) echo "invalid BRIEF_PROFILE=$PROFILE" >&2; exit 2 ;; esac

cd "$PROJECT"
mkdir -p data out logs state
D=$(date +%F)
BASE="$D.$MODE"
START_EPOCH=$(date +%s)
LOCK="$PROJECT/logs/.run.lock"
STAGE=""
STDERR=""
FAILED=0
COST_LOGGED=0

[ -n "$CLAUDE" ] && [ -x "$CLAUDE" ] || { echo "market-brief: claude missing; set CLAUDE_BIN" >&2; exit 1; }
[ -x "$PY" ] || { echo "market-brief: venv python missing at $PY" >&2; exit 1; }
command -v jq >/dev/null || { echo "market-brief: jq is required" >&2; exit 1; }

notify() {
  "$PY" notify.py --status "$1" --message "$2" >> "$PROJECT/logs/notify.log" 2>&1 || true
}

publish_cloud() {
  [ -n "${MARKET_BRIEF_GCS_BUCKET:-}" ] || return 0
  local args=(--project-dir "$PROJECT" --bucket "$MARKET_BRIEF_GCS_BUCKET" --base "$BASE")
  [ -n "${GCP_PROJECT_ID:-}" ] && args+=(--project-id "$GCP_PROJECT_ID")
  "$PY" cloud_publish.py "${args[@]}" >> "$PROJECT/logs/cloud-publish.log" 2>&1
}

run_with_timeout() {
  local seconds=$1
  shift
  "$@" &
  local command_pid=$!
  (
    sleep "$seconds"
    if kill -0 "$command_pid" 2>/dev/null; then
      kill -TERM "$command_pid" 2>/dev/null || true
      sleep 10
      kill -KILL "$command_pid" 2>/dev/null || true
    fi
  ) &
  local watchdog_pid=$!
  local rc=0
  wait "$command_pid" || rc=$?
  kill "$watchdog_pid" 2>/dev/null || true
  wait "$watchdog_pid" 2>/dev/null || true
  return "$rc"
}

write_status() {
  local state=$1 reason=$2 report=${3:-}
  local tmp
  tmp=$(mktemp "$PROJECT/out/.status.XXXXXX")
  jq -n --arg state "$state" --arg reason "$reason" --arg date "$D" \
    --arg mode "$MODE" --arg profile "$PROFILE" --arg report "$report" \
    --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
    '{state:$state,reason:$reason,date:$date,mode:$mode,profile:$profile,
      report:($report|select(length>0)),updated_at:$at}' > "$tmp"
  mv "$tmp" "out/status.$MODE.json"
}

append_cost() {
  local status=$1
  [ "$COST_LOGGED" -eq 0 ] || return 0
  [ -n "${RUN_JSON:-}" ] && [ -s "${RUN_JSON:-}" ] || return 0
  jq -e . "$RUN_JSON" >/dev/null 2>&1 || return 0
  local duration
  duration=$(($(date +%s) - START_EPOCH))
  if [ ! -f cost.log ]; then
    printf 'run_at_utc\tdate\tmode\tprofile\tstatus\tduration_seconds\ttotal_cost_usd\tnum_turns\tmodel_usage_json\n' > cost.log
  fi
  {
    printf '%s\t%s\t%s\t%s\t%s\t%s\t' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$D" "$MODE" "$PROFILE" "$status" "$duration"
    jq -rj '[.total_cost_usd // 0, .num_turns // 0] | @tsv' "$RUN_JSON"
    printf '\t'
    jq -c '.modelUsage // {}' "$RUN_JSON"
    printf '\n'
  } >> cost.log
  COST_LOGGED=1
}

cleanup() {
  local rc=$?
  trap - EXIT ERR INT TERM
  [ -n "$STAGE" ] && [ -d "$STAGE" ] && rm -rf "$STAGE"
  [ -d "$LOCK" ] && [ -f "$LOCK/pid" ] && [ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LOCK"
  exit "$rc"
}

fail() {
  local reason=$1
  FAILED=1
  trap - ERR
  set +e
  local tmp="out/.$BASE.FAILED.tmp"
  {
    echo "reason: $reason"
    echo "date: $D"
    echo "mode: $MODE"
    echo "profile: $PROFILE"
    echo "failed_at_utc: $(date -u +%Y-%m-%dT%H:%M:%SZ)"
    [ -n "$STDERR" ] && [ -f "$STDERR" ] && { echo "--- stderr ---"; cat "$STDERR"; }
  } > "$tmp"
  mv "$tmp" "out/$BASE.FAILED.txt"
  if [ -n "${RUN_JSON:-}" ] && [ -s "${RUN_JSON:-}" ]; then
    cp "$RUN_JSON" "out/$BASE.FAILED.run.json"
  fi
  append_cost failed
  write_status failed "$reason" "out/$BASE.md"
  echo "market-brief FAILED ($BASE): $reason" >&2
  notify FAILED "$BASE — $reason"
  exit 1
}

unexpected_error() {
  local rc=$? line=$1
  [ "$FAILED" -eq 1 ] && exit "$rc"
  fail "unexpected shell error at run.sh:$line (exit $rc)"
}

trap cleanup EXIT
trap 'unexpected_error $LINENO' ERR
trap 'fail "interrupted by signal"' INT TERM

# Atomic mkdir lock.  Recover only a demonstrably stale lock.
if ! mkdir "$LOCK" 2>/dev/null; then
  OLD_PID=$(cat "$LOCK/pid" 2>/dev/null || true)
  if [ -n "$OLD_PID" ] && ! kill -0 "$OLD_PID" 2>/dev/null; then
    rm -rf "$LOCK"
    mkdir "$LOCK"
  else
    echo "market-brief: another run is active (pid ${OLD_PID:-unknown})" >&2
    exit 0
  fi
fi
echo $$ > "$LOCK/pid"

# The scheduler is idempotent: never spend twice for an already-published mode.
if [ "$SCHEDULED" = "--scheduled" ] && { [ -f "out/$BASE.md" ] || [ -f "out/$BASE.FAILED.txt" ]; }; then
  exit 0
fi

STAGE=$(mktemp -d "$PROJECT/out/.stage.$BASE.XXXXXX")
STDERR="$STAGE/stderr.log"
CONTEXT="$STAGE/context.json"

CLOCK_ARGS=(--date "$D" --mode "$MODE")
if [ "${MARKET_BRIEF_FORCE:-0}" = 1 ]; then
  CLOCK_ARGS+=(--allow-outside-window)
fi
set +e
trap - ERR
"$PY" market_clock.py "${CLOCK_ARGS[@]}" > "$CONTEXT" 2>> "$STDERR"
CLOCK_RC=$?
set -e
trap 'unexpected_error $LINENO' ERR
if [ "$CLOCK_RC" -eq 3 ] && [ "$SCHEDULED" = "--scheduled" ]; then
  exit 0
elif [ "$CLOCK_RC" -ne 0 ]; then
  fail "market window rejected this run (use MARKET_BRIEF_FORCE=1 for an intentional manual override)"
fi

DATA_STAGE="$STAGE/$BASE.json"
RAW_DATA_STAGE="$STAGE/$BASE.raw.json"
run_with_timeout 180 "$PY" fetch_data.py --date "$D" --mode "$MODE" --context "$CONTEXT" \
  --out "$DATA_STAGE" --raw-out "$RAW_DATA_STAGE" 2>> "$STDERR" \
  || fail "deterministic data fetch or freshness gate failed"

HISTORY_STAGE="$STAGE/$BASE.history.json"
"$PY" history_engine.py --db "$STATE_DB" context \
  --data "$DATA_STAGE" --out "$HISTORY_STAGE" 2>> "$STDERR" \
  || fail "historical comparison context failed"

if [ "$PROFILE" = deep ]; then
  WRITE_MODEL=opus
  FALLBACK_MODEL=sonnet
  WRITE_EFFORT=high
  BUDGET=6.50
  MAX_TURNS=90
else
  WRITE_MODEL=sonnet
  FALLBACK_MODEL=opus
  WRITE_EFFORT=high
  BUDGET=4.50
  MAX_TURNS=72
fi

PROMPT='Run /market-brief with args {"date":"'"$D"'","mode":"'"$MODE"'","profile":"'"$PROFILE"'","dataPath":"'"$DATA_STAGE"'","historyPath":"'"$HISTORY_STAGE"'","writeModel":"'"$WRITE_MODEL"'","writeEffort":"'"$WRITE_EFFORT"'","fallbackWriteModel":"'"$FALLBACK_MODEL"'"}. When the workflow returns, output its returned text as your ENTIRE final message — verbatim and complete, including the <<RANK_JSON_BEGIN>> ... <<RANK_JSON_END>> block. Do not summarize, reformat, translate, or omit any part; no preamble.'

RUN_JSON="$STAGE/$BASE.run.json"
set +e
trap - ERR
run_with_timeout 900 "$CLAUDE" -p "$PROMPT" \
  --model sonnet --effort medium --permission-mode dontAsk \
  --allowedTools "Read" "WebSearch" "WebFetch" "Skill" "Workflow" \
  --disallowedTools "Write" "Edit" "NotebookEdit" "Bash" \
  --max-turns "$MAX_TURNS" --max-budget-usd "$BUDGET" \
  --no-session-persistence --output-format json > "$RUN_JSON" 2>> "$STDERR"
CLAUDE_RC=$?
set -e
trap 'unexpected_error $LINENO' ERR

# A workflow can finish and persist its journal while the outer Claude relay
# times out or fails to return the result. Recover only an exact match for this
# run; all ordinary report gates below still apply.
NEEDS_RECOVERY=0
if [ "$CLAUDE_RC" -ne 0 ] || ! jq -e \
  '.is_error == false and (.result | type == "string") and (.result | contains("<<RANK_JSON_BEGIN>>")) and (.result | contains("<<RANK_JSON_END>>"))' \
  "$RUN_JSON" >/dev/null 2>&1; then
  NEEDS_RECOVERY=1
fi
if [ "$NEEDS_RECOVERY" -eq 1 ]; then
  set +e
  "$PY" recover_workflow.py --projects-root "$CLAUDE_PROJECTS_ROOT" \
    --data-path "$DATA_STAGE" --history-path "$HISTORY_STAGE" \
    --started-after "$START_EPOCH" --run-json "$RUN_JSON" >> "$STDERR" 2>&1
  RECOVERY_RC=$?
  set -e
  if [ "$RECOVERY_RC" -eq 0 ]; then
    CLAUDE_RC=0
  fi
fi

if [ "$CLAUDE_RC" -ne 0 ]; then
  if jq -e . "$RUN_JSON" >/dev/null 2>&1; then
    CLAUDE_MESSAGE=$(jq -r '.result // "unknown Claude error"' "$RUN_JSON" | tr '\n' ' ' | cut -c1-240)
    fail "claude error: $CLAUDE_MESSAGE"
  fi
  fail "claude -p exited non-zero (exit $CLAUDE_RC)"
fi

jq -e '.is_error == false' "$RUN_JSON" >/dev/null 2>&1 \
  || fail "claude reported is_error"
jq -e '(.permission_denials // []) | length == 0' "$RUN_JSON" >/dev/null 2>&1 \
  || fail "permission denial occurred"

FULL_STAGE="$STAGE/$BASE.full.md"
REPORT_STAGE="$STAGE/$BASE.md"
RANK_STAGE="$STAGE/$BASE.rank.json"
jq -r '.result' "$RUN_JSON" > "$FULL_STAGE"
if ! grep -q '<<RANK_JSON_BEGIN>>' "$FULL_STAGE"; then
  WORKFLOW_SUMMARY=$(jq -r '.result // "workflow returned no result"' "$RUN_JSON" \
    | tr '\n' ' ' | cut -c1-220)
  fail "workflow returned no rank block: $WORKFLOW_SUMMARY"
fi
grep -q '<<RANK_JSON_END>>' "$FULL_STAGE" || fail "rank block truncated"
sed '/<<RANK_JSON_BEGIN>>/,$d' "$FULL_STAGE" > "$REPORT_STAGE"
sed -n '/<<RANK_JSON_BEGIN>>/,/<<RANK_JSON_END>>/p' "$FULL_STAGE" | sed '1d;$d' > "$RANK_STAGE"
jq -e . "$RANK_STAGE" >/dev/null 2>&1 || fail "rank block is not valid JSON"

WANT=$(jq -r '.report_chars // empty' "$RANK_STAGE")
GOT=$(wc -m < "$REPORT_STAGE" | tr -d ' ')
[ -n "$WANT" ] || fail "rank block missing report_chars"
awk -v w="$WANT" -v g="$GOT" 'BEGIN{ if (w+0<=0 || g<w*0.9 || g>w*1.1) exit 1 }' \
  || fail "relay length mismatch: writer said ~$WANT chars, received $GOT"

"$PY" validate_report.py --report "$REPORT_STAGE" --rank "$RANK_STAGE" \
  --data "$DATA_STAGE" --history "$HISTORY_STAGE" \
  --mode "$MODE" --profile "$PROFILE" 2>> "$STDERR" \
  || fail "deterministic report publication gate failed"

# Publish machine artifacts first. The historical transaction and metrics must
# pass before the human report moves; the report remains the success marker.
mv "$DATA_STAGE" "data/$BASE.json"
mv "$RAW_DATA_STAGE" "data/$BASE.raw.json"
mv "$RUN_JSON" "out/$BASE.run.json"
RUN_JSON="out/$BASE.run.json"
mv "$FULL_STAGE" "out/$BASE.full.md"
mv "$RANK_STAGE" "out/$BASE.rank.json"
mv "$HISTORY_STAGE" "out/$BASE.history.json"

"$PY" history_engine.py --db "$STATE_DB" ingest \
  --data "data/$BASE.json" --rank "out/$BASE.rank.json" \
  --report "out/$BASE.md" --run-json "out/$BASE.run.json" --profile "$PROFILE" \
  2>> "$STDERR" || fail "SQLite history ingest failed"
METRICS_STAGE="$STAGE/metrics.json"
"$PY" history_engine.py --db "$STATE_DB" metrics --out "$METRICS_STAGE" \
  2>> "$STDERR" || fail "historical quality metrics failed"
mv "$METRICS_STAGE" "out/metrics.json"

mv "$REPORT_STAGE" "out/$BASE.md"
rm -f "out/$BASE.FAILED.txt"
rm -f "out/$BASE.FAILED.run.json"

ln -sfn "$BASE.md" "out/latest.$MODE.md"
ln -sfn "$BASE.md" "out/latest.md"

END_EPOCH=$(date +%s)
DURATION=$((END_EPOCH - START_EPOCH))
append_cost success

DEG=$(jq -r 'if .degraded then " [DEGRADED]" else "" end' "out/$BASE.rank.json")
write_status success "publication gates passed" "out/$BASE.md"
echo "market-brief: wrote out/$BASE.md ($GOT chars, ${DURATION}s)$DEG"
if publish_cloud; then
  notify SUCCESS "$BASE — ${GOT} chars, ${DURATION}s$DEG"
else
  notify DELIVERY_FAILED "$BASE 已在服务器生成，但同步至手机查看器失败"
fi
