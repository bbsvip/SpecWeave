#!/bin/bash
set -eu
cd /app
# Fail before migrations/startup if Fernet key is invalid; never print the secret.
PYTHONPATH=/app/source python -c "from security import validate_encryption_key; validate_encryption_key()"
python -m alembic -c /app/alembic.ini upgrade head
exec python source/app.py
