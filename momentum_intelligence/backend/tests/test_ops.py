"""Phase 11: backup/restore tooling. The full drill (scripts/restore_drill.sh) runs against a real database;
these tests prove the fingerprint detects a changed row and that restore refuses unsafe use."""

import os
import pathlib
import shutil
import subprocess

import pytest

from sqlalchemy import text

from app.database import get_engine
from app.ops.fingerprint import compare, fingerprint

SCRIPTS = pathlib.Path(__file__).resolve().parents[2] / "scripts"


def test_fingerprint_detects_a_single_changed_row():
    url = os.environ["MI_DATABASE_URL"]
    before = fingerprint(url)
    with get_engine().begin() as c:
        c.execute(text("insert into app_settings (key, value, changes, note, updated_by, updated_at) values ('fp-test', '{}', '[]', 'x', 'test', now())"))
    try:
        after = fingerprint(url)
        diffs = compare(before, after)
        assert len(diffs) == 1 and diffs[0].startswith("app_settings")
    finally:
        with get_engine().begin() as c:
            c.execute(text("delete from app_settings where key = 'fp-test'"))
    assert compare(before, fingerprint(url)) == []


def run(*args, **env):
    return subprocess.run(["bash", str(SCRIPTS / "restore.sh"), *args], capture_output=True, text=True,
                          env={**os.environ, **env})


def test_restore_refuses_without_confirmation_and_checks_names(tmp_path):
    f = tmp_path / "x.dump"
    f.write_bytes(b"not a dump")
    r = run(str(f), "--target", "mi_x")
    assert r.returncode == 2 and "--yes" in r.stderr
    r = run(str(f), "--target", "bad;name", "--yes")
    assert r.returncode == 2 and "invalid database name" in r.stderr
    r = run(str(tmp_path / "missing.dump"), "--target", "mi_x", "--yes")
    assert r.returncode == 2


def test_restore_rejects_a_corrupted_backup(tmp_path):
    f = tmp_path / "x.dump"
    f.write_bytes(b"not a dump")
    (tmp_path / "x.dump.sha256").write_text("0" * 64 + "  x.dump\n")
    r = run(str(f), "--target", "mi_x", "--yes")
    assert r.returncode == 1 and "checksum mismatch" in r.stderr


@pytest.mark.skipif(shutil.which("psql") is None, reason="needs the PostgreSQL client tools")
def test_restore_will_not_overwrite_a_database_with_tables_unless_told(tmp_path):
    f = tmp_path / "x.dump"
    f.write_bytes(b"irrelevant")
    target = os.environ["MI_DATABASE_URL"].rsplit("/", 1)[1]   # the test database, which has tables
    r = run(str(f), "--target", target, "--yes")
    assert r.returncode == 1 and "--replace" in r.stderr


def test_dotenv_loader_does_not_abort_scripts_when_keys_are_already_set(tmp_path):
    # regression: `[ -z ... ] && export` made the loader return 1 when the last key was already set, which
    # silently stopped any `set -e` script called from another script (the restore drill)
    (tmp_path / ".env").write_text("MI_A=from-file\nMI_B=from-file\n")
    script = f'set -euo pipefail; source "{SCRIPTS / "_pg_env.sh"}"; load_dotenv; echo "$MI_A $MI_B"'
    r = subprocess.run(["bash", "-c", script], cwd=tmp_path, capture_output=True, text=True,
                       env={**os.environ, "MI_B": "from-env"})
    assert r.returncode == 0 and r.stdout.strip() == "from-file from-env"
