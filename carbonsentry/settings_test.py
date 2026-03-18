from carbonsentry.settings import *   # inherit everything from main settings

# Use SQLite instead of Postgres — no server needed, runs in RAM
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",   # wiped after every test run
    }
}

# No real emails sent — mail.outbox captures them instead
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

# MD5 is weak but ~100x faster than bcrypt — fine for tests
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# No Redis needed
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


PROMETHEUS_URL = ""   # force DB fallback in tests
GRAFANA_URL    = ""