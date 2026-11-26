"""Phase 11: the schema builds from an empty database, matches the models, and downgrades cleanly."""

import os
import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url

from app.models import Base

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture
def empty_db(monkeypatch):
    base = make_url(os.environ["MI_DATABASE_URL"])
    name = f"mi_migration_{uuid.uuid4().hex[:8]}"
    admin = create_engine(base.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        with admin.connect() as c:
            c.execute(text(f'create database "{name}"'))
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"cannot create a throw-away database ({type(e).__name__}); grant CREATEDB to run this test")
    url = base.set(database=name).render_as_string(hide_password=False)
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "database_url", url)   # migrations/env.py reads the URL from settings
    try:
        yield url
    finally:
        with admin.connect() as c:
            c.execute(text(f'drop database if exists "{name}" with (force)'))
        admin.dispose()


def test_upgrade_from_empty_matches_models_and_round_trips(empty_db):
    cfg = Config(os.path.join(BACKEND, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND, "migrations"))
    command.upgrade(cfg, "head")
    eng = create_engine(empty_db)
    try:
        with eng.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
            tables = set(inspect(conn).get_table_names())
        assert not diff, f"models and migrations differ: {diff}"
        assert set(Base.metadata.tables) <= tables
        command.downgrade(cfg, "base")
        with eng.connect() as conn:
            assert set(inspect(conn).get_table_names()) <= {"alembic_version"}
        command.upgrade(cfg, "head")
        with eng.connect() as conn:
            assert conn.execute(text("select version_num from alembic_version")).scalar() is not None
    finally:
        eng.dispose()
