import pytest
from sqlalchemy import text

from app.database import get_engine, ping


@pytest.mark.db
def test_postgres_test_database_is_reachable():
    ok, detail = ping()
    if not ok:
        pytest.skip(f"PostgreSQL test database not reachable ({detail}); run scripts/dev_db_setup.sh")
    with get_engine().connect() as conn:
        assert conn.execute(text("select current_database()")).scalar_one() == "momentum_test"
