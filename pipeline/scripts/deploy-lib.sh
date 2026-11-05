# Sourced by pipeline/scripts/deploy-live.sh and services/coach/scripts/deploy-live.sh.
# Caller sets LIVE_DIR. Both deploys move the same checkout, so both run these.

CONSOLE_UNIT=painforwisdom-console.service
CONSOLE_URL="http://127.0.0.1:8820/api/graph"

# Prints the run_id of the first run.json with an active status and a live pid
# (same rule as pipeline/ui/runs.py; contract C2).
active_pipeline_run() {
  python3 - "${PFW_PROCESSED_ROOT:-$LIVE_DIR/processed}" <<'PY'
import glob, json, os, sys

for path in sorted(glob.glob(os.path.join(sys.argv[1], "*", "langgraph", "run.json"))):
    try:
        with open(path) as f:
            rec = json.load(f)
        pid = rec["pid"]
        if rec["status"] not in ("running", "waiting_hitl", "waiting_reply") or not isinstance(pid, int) or pid <= 0:
            continue
        os.kill(pid, 0)
    except PermissionError:
        pass
    except (OSError, OverflowError, ValueError, KeyError, TypeError):
        continue
    print(path.split(os.sep)[-3])
    break
PY
}

# Agent prompts are read from disk at node time: a checkout mid-run would mix versions.
refuse_active_run() {
  [ "${DEPLOY_IGNORE_ACTIVE_RUN:-0}" != "1" ] || return 0
  local run
  run="$(active_pipeline_run)"
  if [ -n "$run" ]; then
    echo "pipeline run $run active; retry after it ends"
    exit 1
  fi
}

# Installs units from the checked-out tag, restarts the console, waits for it.
deploy_console() {
  echo "==> install pipeline units from $1"
  PFW_LIVE_DIR="$LIVE_DIR" bash "$LIVE_DIR/pipeline/scripts/install-pipeline-units.sh" --no-restart
  echo "==> restart $CONSOLE_UNIT"
  systemctl --user restart "$CONSOLE_UNIT"

  echo "==> wait for console ($CONSOLE_URL)"
  local up=0 _
  SECONDS=0
  for _ in $(seq 1 30); do
    if curl -fsS -m 2 "$CONSOLE_URL" >/dev/null 2>&1; then
      up=1
      echo "console up after ${SECONDS}s"
      break
    fi
    if [ "$SECONDS" -ge 30 ]; then break; fi
    sleep 1
  done
  if [ "$up" != 1 ]; then
    echo "FAIL: console timeout at $CONSOLE_URL (journalctl --user -u $CONSOLE_UNIT -n 50)"
    exit 1
  fi
}
