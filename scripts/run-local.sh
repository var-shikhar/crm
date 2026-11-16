#!/bin/sh
# Run the whole CRM on this machine without Docker.
#   ./scripts/run-local.sh start | stop | status | logs
# Uses SQLite files under .local/ and prints one-time email codes to .local/logs/auth.log.
set -eu
cd "$(dirname "$0")/.."
ROOT=$(pwd)
RUN="$ROOT/.local"
PY="$ROOT/.venv/bin/python"

setup() {
  mkdir -p "$RUN/logs" "$RUN/pids"
  if [ ! -x "$PY" ]; then
    echo "Creating virtualenv..."
    uv venv --python 3.13 "$ROOT/.venv" -q
    uv pip install -q --python "$PY" -r services/auth-service/requirements.txt pytest
  fi
  if [ ! -f "$RUN/env" ]; then
    rand() { LC_ALL=C tr -dc 'A-Za-z0-9' < /dev/urandom | head -c "$1"; }
    cat > "$RUN/env" <<ENV
SECRET_KEY=$(rand 48)
CUSTOMER_SERVICE_CLIENT_SECRET=$(rand 40)
AUDIT_SERVICE_CLIENT_SECRET=$(rand 40)
BOOTSTRAP_ADMIN_EMAIL=admin@example.com
BOOTSTRAP_ADMIN_PASSWORD=$(rand 20)
ENV
    chmod 600 "$RUN/env"
  fi
  set -a; . "$RUN/env"; set +a
}

launch() {  # name port appdir app  (service env comes from the caller's environment)
  name=$1; port=$2; dir=$3
  if [ -f "$RUN/pids/$name" ] && kill -0 "$(cat "$RUN/pids/$name")" 2>/dev/null; then
    echo "$name already running"; return
  fi
  ( cd "$dir"; PYTHONPATH="$ROOT/shared" nohup "$PY" -m uvicorn "$4" --host 127.0.0.1 --port "$port" \
      --root-path "${ROOT_PATH:-}" --no-access-log < /dev/null >> "$RUN/logs/$name.log" 2>&1 & echo $! > "$RUN/pids/$name" )
}

wait_for() {  # url
  i=0
  until curl -fsS -o /dev/null "$1" 2>/dev/null; do
    i=$((i + 1)); [ "$i" -gt 60 ] && { echo "timed out waiting for $1" >&2; return 1; }
    sleep 0.5
  done
}

start() {
  setup
  DATABASE_URL="sqlite:///$RUN/auth.db" ROOT_PATH=/auth ISSUER=http://localhost:8080/auth \
    JWT_PRIVATE_KEY_PATH="$RUN/keys/jwt_private.pem" EMAIL_BACKEND=console AUDIT_URL=http://127.0.0.1:8003 \
    launch auth 8001 services/auth-service app.main:app
  wait_for http://127.0.0.1:8001/readyz

  DATABASE_URL="sqlite:///$RUN/audit.db" ROOT_PATH=/audit AUTH_URL=http://127.0.0.1:8001 \
    CLIENT_ID=audit-service CLIENT_SECRET="$AUDIT_SERVICE_CLIENT_SECRET" \
    launch audit 8003 services/audit-service app.main:app

  DATABASE_URL="sqlite:///$RUN/customer.db" ROOT_PATH=/crm AUTH_URL=http://127.0.0.1:8001 \
    AUDIT_URL=http://127.0.0.1:8003 CLIENT_ID=customer-service CLIENT_SECRET="$CUSTOMER_SERVICE_CLIENT_SECRET" \
    launch customer 8002 services/customer-service app.main:app

  launch gateway 8080 scripts dev_gateway:app
  wait_for http://127.0.0.1:8003/readyz
  wait_for http://127.0.0.1:8002/readyz
  wait_for http://127.0.0.1:8080/auth/healthz

  echo
  echo "CRM is running:  http://localhost:8080"
  echo "  API docs:      http://localhost:8080/auth/docs   /crm/docs   /audit/docs"
  echo "  Sign in as:    $BOOTSTRAP_ADMIN_EMAIL / $BOOTSTRAP_ADMIN_PASSWORD"
  echo "  Email codes:   ./scripts/run-local.sh code     (printed to .local/logs/auth.log)"
}

stop() {
  for f in "$RUN"/pids/*; do
    [ -f "$f" ] || continue
    pid=$(cat "$f"); kill "$pid" 2>/dev/null && echo "stopped $(basename "$f") ($pid)"; rm -f "$f"
  done
}

status() {
  for name in auth customer audit gateway; do
    if [ -f "$RUN/pids/$name" ] && kill -0 "$(cat "$RUN/pids/$name")" 2>/dev/null; then
      echo "$name: running (pid $(cat "$RUN/pids/$name"))"
    else
      echo "$name: stopped"
    fi
  done
}

case "${1:-start}" in
  start) start ;;
  stop) stop ;;
  restart) stop; sleep 1; start ;;
  status) status ;;
  logs) tail -n 50 -f "$RUN"/logs/*.log ;;
  code) grep -o 'verification code to sign in is: [0-9]*' "$RUN/logs/auth.log" | tail -1 ;;
  reset) stop; rm -rf "$RUN"; echo "removed .local (databases, keys, logs)" ;;
  *) echo "usage: $0 start|stop|restart|status|logs|code|reset" >&2; exit 2 ;;
esac
