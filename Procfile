web: daphne -b 0.0.0.0 -p $PORT carbonsentry.asgi:application
worker: celery -A carbonsentry worker --loglevel=info --concurrency=2