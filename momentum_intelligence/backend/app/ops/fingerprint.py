"""Content fingerprint of a database: row count and an order-independent hash of every row, per table.

Two databases with equal fingerprints hold the same rows (up to a hash collision). Used by the restore drill
(scripts/restore_drill.sh) to prove a restore is identical, not merely that pg_restore exited 0.

    python -m app.ops.fingerprint                 # database from MI_DATABASE_URL
    python -m app.ops.fingerprint --database other_db --out fp.json   # same server and credentials
    python -m app.ops.fingerprint --compare a.json b.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone


def fingerprint(url: str) -> dict:
    from sqlalchemy import create_engine, inspect, text   # imported here so --compare needs only the stdlib
    from sqlalchemy.engine import make_url
    eng = create_engine(url)
    try:
        with eng.connect() as conn:
            tables = sorted(inspect(conn).get_table_names())
            out = {}
            for t in tables:
                # hashtextextended is a 64-bit hash; summing as numeric cannot overflow and ignores row order
                r = conn.execute(text(
                    f'select count(*), coalesce(sum(hashtextextended(x::text, 0)::numeric), 0) from "{t}" x')).one()
                out[t] = {"rows": int(r[0]), "hash": str(r[1])}
            version = conn.execute(text("select version_num from alembic_version")).scalar() if "alembic_version" in tables else None
    finally:
        eng.dispose()
    return {"database": make_url(url).database, "taken_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "alembic_version": version, "tables": out}


def compare(a: dict, b: dict) -> list[str]:
    diffs = []
    if a.get("alembic_version") != b.get("alembic_version"):
        diffs.append(f"alembic_version {a.get('alembic_version')} != {b.get('alembic_version')}")
    for t in sorted(set(a["tables"]) | set(b["tables"])):
        x, y = a["tables"].get(t), b["tables"].get(t)
        if x != y:
            diffs.append(f"{t}: {x} != {y}")
    return diffs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fingerprint")
    ap.add_argument("--url")
    ap.add_argument("--database", help="fingerprint another database on the same server (keeps the password off the command line)")
    ap.add_argument("--out")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    a = ap.parse_args(argv)
    if a.compare:
        x, y = (json.load(open(p)) for p in a.compare)
        diffs = compare(x, y)
        rows = sum(v["rows"] for v in x["tables"].values())
        print(json.dumps({"identical": not diffs, "tables": len(x["tables"]), "rows": rows, "differences": diffs}, indent=2))
        return 0 if not diffs else 1
    from ..config import get_settings
    url = a.url or get_settings().database_url
    if a.database:
        from sqlalchemy.engine import make_url
        url = make_url(url).set(database=a.database).render_as_string(hide_password=False)
    fp = fingerprint(url)
    s = json.dumps(fp, indent=2)
    if a.out:
        with open(a.out, "w") as f:
            f.write(s)
    else:
        print(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
