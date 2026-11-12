#!/bin/sh
set -eu
cd /app
python -m alembic -c /app/alembic.ini upgrade head
exec python source/app.py
