#!/bin/bash
set -e

echo "Waiting for database..."
python - <<'PYEOF'
import os, time, psycopg2
for _ in range(30):
    try:
        psycopg2.connect(
            dbname=os.getenv("DB_NAME"), user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"), host=os.getenv("DB_HOST"),
            port=os.getenv("DB_PORT"),
        ).close()
        break
    except psycopg2.OperationalError:
        time.sleep(1)
else:
    raise SystemExit("Database not reachable")
PYEOF

if [ "$1" = "web" ]; then
    python manage.py migrate --noinput
    python manage.py collectstatic --noinput
    exec daphne -b 0.0.0.0 -p 8000 carbonsentry.asgi:application
elif [ "$1" = "celery" ]; then
    exec celery -A carbonsentry worker -l info
else
    exec "$@"
fi