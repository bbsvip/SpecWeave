#!/bin/bash
set -eu
cd /app
# Fail before migrations/startup if Fernet key is invalid; never print the secret.
PYTHONPATH=/app/source python -c "from security import validate_encryption_key; validate_encryption_key()"
python -m alembic -c /app/alembic.ini upgrade head
node source/pi_host.mjs &
pi_pid=$!
python source/app.py &
web_pid=$!

stop_services() {
    kill "$pi_pid" "$web_pid" 2>/dev/null || true
    wait "$pi_pid" "$web_pid" 2>/dev/null || true
}
trap stop_services EXIT
trap 'exit 143' TERM
trap 'exit 130' INT
# Restart the web container if either of its services exits.
wait -n "$pi_pid" "$web_pid"
exit 1
