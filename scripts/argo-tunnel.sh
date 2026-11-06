#!/usr/bin/env bash
#
# argo-tunnel.sh -- reach the ANL Argo gateway (frontier models) from a Fermilab host.
#
# Argo is only reachable from inside ANL, so `argo-proxy` (an OpenAI-compatible
# front end) runs on the ANL login node and this host talks to it through an
# ssh port forward. `start` makes sure both halves are up:
#
#   1. argo-proxy on the login node, detached with setsid/nohup (the login node
#      has no tmux or screen). It must listen on 127.0.0.1 only -- Argo trusts
#      the username alone, so a proxy on 0.0.0.0 lets anyone on the ANL network
#      make calls as you. Set `host: 127.0.0.1` in ~/.config/argoproxy/config.yaml.
#   2. A local supervisor loop that holds the ssh forward open and reconnects
#      (restarting argo-proxy if needed) whenever it drops.
#
# Point kb-mcp at it per model, so the existing endpoint stays the default:
#   OPENAI_BASE_URL_MODELS='{"argo:claude-opus-5": "http://127.0.0.1:64259/v1", ...}'
#   OPENAI_API_KEY_MODELS='{"argo:claude-opus-5": "argo", ...}'   # any non-empty value
#
# Interactive use only: it needs your ssh config and keys, which the cron/server
# host does not have.
#
set -euo pipefail

HOST="${ARGO_SSH_HOST:-anl-login}"
PORT="${ARGO_PORT:-64259}"
STATE="${ARGO_STATE_DIR:-/tmp/$USER/argo-tunnel}"
PIDFILE="$STATE/supervisor.pid"
LOG="$STATE/tunnel.log"

usage() {
  cat >&2 <<'USAGE'
Usage: argo-tunnel.sh start|stop|status|restart

  start    Start argo-proxy on the ANL login node if needed and keep an ssh
           forward to it open in the background (reconnects on drop).
  stop     Stop the local tunnel. argo-proxy on the login node is left running;
           `argo-tunnel.sh stop-remote` stops it too.
  status   Report whether the supervisor runs and the proxy answers.

Environment: ARGO_SSH_HOST (anl-login), ARGO_PORT (64259), ARGO_STATE_DIR.
USAGE
  exit 2
}

# ssh for one-off remote commands. ClearAllForwardings keeps the LocalForward
# in ~/.ssh/config from colliding with the tunnel's port.
remote() {
  ssh -o BatchMode=yes -o ConnectTimeout=15 -o ClearAllForwardings=yes -o LogLevel=ERROR \
    "$HOST" "$@" 2>/dev/null
}

proxy_answers() {
  curl -s -m 5 -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/v1/models" | grep -q '^200$'
}

ensure_remote_proxy() {
  # `ss` rather than pgrep: what matters is that something is listening.
  if remote "ss -ltn | grep -q '127.0.0.1:$PORT '"; then
    return 0
  fi
  if remote "ss -ltn | grep -qE '(0.0.0.0|\\*|\\[::\\]):$PORT '"; then
    echo "$(date '+%F %T') refusing: something on $HOST listens on $PORT on all interfaces; set host: 127.0.0.1" >>"$LOG"
    return 1
  fi
  echo "$(date '+%F %T') starting argo-proxy on $HOST" >>"$LOG"
  remote "setsid nohup argo-proxy serve >~/argo-proxy.log 2>&1 </dev/null & sleep 5; ss -ltn | grep -q '127.0.0.1:$PORT '"
}

supervise() {
  while true; do
    if ensure_remote_proxy; then
      echo "$(date '+%F %T') opening forward to $HOST" >>"$LOG"
      # No ExitOnForwardFailure: a LocalForward for the same port in ~/.ssh/config
      # makes one of the two binds fail harmlessly; `status` checks the real thing.
      ssh -N -o BatchMode=yes -o ConnectTimeout=15 \
        -o ServerAliveInterval=30 -o ServerAliveCountMax=3 \
        -L "127.0.0.1:$PORT:127.0.0.1:$PORT" "$HOST" >>"$LOG" 2>&1 || true
      echo "$(date '+%F %T') forward dropped" >>"$LOG"
    fi
    sleep 15
  done
}

supervisor_running() {
  [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null
}

cmd_start() {
  mkdir -p "$STATE"
  chmod 700 "$STATE"
  if supervisor_running; then
    echo "already running (pid $(cat "$PIDFILE"))"
  else
    setsid "$0" _supervise </dev/null >>"$LOG" 2>&1 &
    echo $! >"$PIDFILE"
    echo "started supervisor (pid $!), log: $LOG"
  fi
  for _ in $(seq 1 20); do
    if proxy_answers; then echo "proxy answers on 127.0.0.1:$PORT"; return 0; fi
    sleep 2
  done
  echo "proxy not answering yet; see $LOG" >&2
  return 1
}

cmd_stop() {
  if supervisor_running; then
    # The supervisor is a session leader (setsid): kill its whole group, ssh included.
    kill -- "-$(cat "$PIDFILE")" 2>/dev/null || kill "$(cat "$PIDFILE")"
    echo "stopped"
  else
    echo "not running"
  fi
  rm -f "$PIDFILE"
}

cmd_status() {
  if supervisor_running; then echo "supervisor: running (pid $(cat "$PIDFILE"))"; else echo "supervisor: not running"; fi
  if proxy_answers; then echo "proxy: answering on 127.0.0.1:$PORT"; else echo "proxy: not answering"; return 1; fi
}

case "${1:-}" in
  start) cmd_start ;;
  stop) cmd_stop ;;
  stop-remote) cmd_stop; remote "pkill -u \$USER -f 'argo-proxy serve'" && echo "argo-proxy stopped on $HOST" ;;
  restart) cmd_stop; cmd_start ;;
  status) cmd_status ;;
  _supervise) supervise ;;
  *) usage ;;
esac
