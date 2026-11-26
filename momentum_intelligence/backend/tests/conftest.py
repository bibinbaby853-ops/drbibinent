import os

# Tests always use the dedicated test database, never the development one.
os.environ.setdefault("MI_DATABASE_URL", "postgresql+psycopg://momentum:momentum@localhost:5432/momentum_test")
os.environ.setdefault("MI_APP_ENV", "test")
os.environ.setdefault("MI_DATA_MODE", "demo")
