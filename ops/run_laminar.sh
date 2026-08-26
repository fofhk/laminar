#!/bin/bash
# De-silenced cron wrapper for the Laminar Stage-1 collector.
#
# Same contract as run_collect.sh, with two changes forced by a per-minute job:
#   * flock — a slow API must not pile runs on top of each other.
#   * alert throttling — at 1440 runs/day an unthrottled alert is a mail flood,
#     so email fires only after ALERT_AFTER consecutive failures and then at
#     most once per ALERT_EVERY seconds. Recovery always sends one OK.
#
# Usage: run_laminar.sh <books|markets|trades|competitiveness|leads|activity>
set -u
ROOT=/opt/farseer
CMD=${1:?usage: run_laminar.sh <books|markets|trades|competitiveness|leads|activity>}
LOG=$ROOT/logs/laminar_$CMD.log
STATE=$ROOT/logs/.laminar_$CMD.state
LOCK=$ROOT/logs/.laminar_$CMD.lock
ALERT_AFTER=3      # consecutive failures before the first email
ALERT_EVERY=3600   # seconds between repeat emails while still failing
mkdir -p "$ROOT/logs"

exec 9>"$LOCK"
flock -n 9 || { echo "$(date -u '+%F %T') skipped: previous run still going" >> "$LOG"; exit 0; }

TS=$(date -u '+%Y-%m-%d %H:%M:%S')
OUT=$("$ROOT/.venv/bin/python" -m laminar.collect "$CMD" 2>&1)
RC=$?

read -r FAILS LAST_MAIL 2>/dev/null < "$STATE" || { FAILS=0; LAST_MAIL=0; }
NOW=$(date +%s)

if [ $RC -ne 0 ] || echo "$OUT" | grep -qE "Traceback|ModuleNotFoundError|No module named"; then
  FAILS=$((FAILS + 1))
  { echo "==== $TS $CMD rc=$RC (consecutive failures: $FAILS) ===="; echo "$OUT"; } >> "$LOG"
  if [ "$FAILS" -ge "$ALERT_AFTER" ] && [ $((NOW - LAST_MAIL)) -ge "$ALERT_EVERY" ]; then
    echo "<p>$FAILS consecutive failures.</p><pre>$OUT</pre>" \
      | "$ROOT/.venv/bin/python" "$ROOT/ops/notify.py" ALERT "laminar $CMD FAILED (rc=$RC)"
    LAST_MAIL=$NOW
  fi
else
  # Recovery is worth an email; routine success is not — the daily report
  # covers freshness, and 1440 green mails a day would train us to ignore them.
  if [ "$FAILS" -ge "$ALERT_AFTER" ]; then
    echo "<p>Recovered after $FAILS consecutive failures.</p><pre>$OUT</pre>" \
      | "$ROOT/.venv/bin/python" "$ROOT/ops/notify.py" OK "laminar $CMD recovered"
  fi
  FAILS=0
  # The per-minute job logs a heartbeat only at the top of the hour — 1440
  # success lines a day would bury the failures. Hourly jobs log every run.
  if [ "$CMD" = books ]; then
    case "$(date -u +%M)" in 0[0-2]) echo "==== $TS $CMD ok: $OUT" >> "$LOG" ;; esac
  else
    echo "==== $TS $CMD ok: $OUT" >> "$LOG"
  fi
fi

echo "$FAILS $LAST_MAIL" > "$STATE"
exit $RC
