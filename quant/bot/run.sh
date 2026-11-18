#!/usr/bin/env bash
# Supervisor for the paper bot.
#   bot/run.sh start      start the supervisor in the background (it starts and restarts the bot)
#                         after a lost bot/state:  start --restore  (from paper_state/)  or  start --fresh
#   bot/run.sh stop       stop bot and supervisor cleanly (open paper positions stay in state/)
#   bot/run.sh restart    stop, then start (use after editing config/setup.toml)
#   bot/run.sh status     compact status (bot/status.py)
#   bot/run.sh supervise  run the supervisor loop in the foreground
#
# Restart policy: a crashed bot is restarted with exponential backoff (2 s .. 300 s, reset after 10
# minutes of healthy running) and the reason is written to logs/supervisor.log. If state/HALT says
# blocks_restart (an unhandled exception: Shield halted and a human must look), the supervisor does
# NOT restart; it waits until a human deletes state/HALT.
set -u
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="${BOT_STATE_DIR:-$ROOT/bot/state}"
LOGS="${BOT_LOG_DIR:-$ROOT/bot/logs}"
PY="${BOT_PYTHON:-python3}"
BOT_CMD="${BOT_CMD:-$PY -m bot.main}"
BACKOFF_START="${BOT_BACKOFF_START:-2}"
BACKOFF_MAX="${BOT_BACKOFF_MAX:-300}"
HALT_POLL="${BOT_HALT_POLL:-10}"
SNAP="${BOT_SNAPSHOT_DIR:-$ROOT/paper_state}"
SUP_PID="$STATE/supervisor.pid"
SUP_LOG="$LOGS/supervisor.log"
mkdir -p "$STATE" "$LOGS"
cd "$ROOT" || exit 1

log() { echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) supervisor: $*" >> "$SUP_LOG"; }

alive() { [ -n "${1:-}" ] && kill -0 "$1" 2>/dev/null; }

halt_blocks() {   # prints the reason and returns 0 when state/HALT forbids a restart
  [ -f "$STATE/HALT" ] || return 1
  "$PY" - "$STATE/HALT" <<'PY'
import json, sys
try:
    d = json.load(open(sys.argv[1]))
except Exception:
    sys.exit(1)          # hand-written HALT: the bot itself starts halted, no need to hold it back
if not d.get("blocks_restart"):
    sys.exit(1)
print("; ".join(f"{h.get('kind')}: {h.get('reason')}" for h in d.get("halts", [])))
PY
}

supervise() {
  echo $$ > "$SUP_PID"
  local backoff="$BACKOFF_START" child="" stopping=0 told=0 code ran start reason
  trap 'stopping=1; [ -n "$child" ] && kill -TERM "$child" 2>/dev/null' TERM INT
  log "supervisor started (pid $$), command: $BOT_CMD"
  while true; do
    if [ "$stopping" = 1 ]; then log "stop requested: supervisor exits"; rm -f "$SUP_PID"; exit 0; fi
    if reason="$(halt_blocks)"; then
      if [ "$told" = 0 ]; then
        log "HALT needs a human - NOT restarting: $reason. Delete $STATE/HALT to resume."
        told=1
      fi
      sleep "$HALT_POLL" & wait $!
      continue
    fi
    if [ "$told" = 1 ]; then log "HALT was removed: resuming"; told=0; backoff="$BACKOFF_START"; fi
    start=$(date +%s)
    log "starting bot"
    $BOT_CMD >> "$LOGS/bot.out" 2>&1 &
    child=$!
    wait "$child"; code=$?
    while [ "$stopping" = 1 ] && alive "$child"; do wait "$child"; code=$?; done
    child=""
    ran=$(( $(date +%s) - start ))
    if [ "$stopping" = 1 ]; then log "bot stopped on request (exit $code) after ${ran}s"; rm -f "$SUP_PID"; exit 0; fi
    case "$code" in
      0) log "bot exited cleanly after ${ran}s: supervisor exits"; rm -f "$SUP_PID"; exit 0 ;;
      2) log "bot refused to run (exit 2: mode is not paper): supervisor exits"; rm -f "$SUP_PID"; exit 2 ;;
      4) log "another bot instance is running (exit 4): supervisor exits"; rm -f "$SUP_PID"; exit 4 ;;
      3) log "bot halted itself (exit 3) after ${ran}s" ;;
      *) if [ "$code" -gt 128 ]; then reason="killed by signal $(( code - 128 ))"; else reason="exit code $code"; fi
         log "bot CRASHED ($reason) after ${ran}s; last output: $(tail -n 3 "$LOGS/bot.out" 2>/dev/null | tr '\n' ' ' | cut -c1-600)" ;;
    esac
    if halt_blocks > /dev/null; then continue; fi
    [ "$ran" -ge 600 ] && backoff="$BACKOFF_START"
    log "restarting in ${backoff}s"
    sleep "$backoff" & wait $!
    backoff=$(( backoff * 2 )); [ "$backoff" -gt "$BACKOFF_MAX" ] && backoff="$BACKOFF_MAX"
  done
}

case "${1:-}" in
  supervise) supervise ;;
  start)
    if alive "$(cat "$SUP_PID" 2>/dev/null)"; then echo "supervisor already running (pid $(cat "$SUP_PID"))"; exit 0; fi
    if [ ! -f "$STATE/books.json" ] && [ -f "$SNAP/MANIFEST.json" ]; then
      # bot/state was lost (container reclaimed?) but a snapshot exists: make the human choose
      case "${2:-}" in
        --restore) "$PY" "$ROOT/bot/snapshot.py" --restore --state-dir "$STATE" --log-dir "$LOGS" --dir "$SNAP" || exit 1 ;;
        --fresh)   echo "starting with new books; the snapshot in $SNAP will be replaced by the new run" ;;
        *) echo "bot/state has no books, but a snapshot exists:"
           "$PY" "$ROOT/bot/snapshot.py" --show --dir "$SNAP"
           echo "choose:  $0 start --restore   (resume from the snapshot)"
           echo "         $0 start --fresh     (ignore it, start new books)"
           exit 3 ;;
      esac
    fi
    nohup setsid "$0" supervise > /dev/null 2>&1 &
    sleep 1
    echo "supervisor started (pid $(cat "$SUP_PID" 2>/dev/null)); logs: $SUP_LOG, $LOGS/bot.log"
    ;;
  stop)
    pid="$(cat "$SUP_PID" 2>/dev/null)"
    if alive "$pid"; then
      kill -TERM "$pid"
      for _ in $(seq 1 40); do alive "$pid" || break; sleep 0.5; done
      alive "$pid" && { echo "supervisor $pid did not stop in 20 s"; exit 1; }
      echo "stopped"
    else
      bpid="$(cat "$STATE/bot.pid" 2>/dev/null)"
      if alive "$bpid"; then kill -TERM "$bpid"; echo "no supervisor; sent SIGTERM to bot $bpid"; else echo "not running"; fi
    fi
    ;;
  restart) "$0" stop; "$0" start ;;
  status) exec "$PY" "$ROOT/bot/status.py" "${@:2}" ;;
  *) echo "usage: $0 start [--restore|--fresh] | stop | restart | status | supervise"; exit 64 ;;
esac
