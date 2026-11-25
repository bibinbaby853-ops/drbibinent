#!/usr/bin/env python3
"""Report whether this machine can run Momentum Intelligence. Read-only; changes nothing.

    python scripts/check_environment.py
"""

from __future__ import annotations

import importlib
import shutil
import socket
import subprocess
import sys
import urllib.request

PACKAGES = ["fastapi", "uvicorn", "pydantic", "pydantic_settings", "sqlalchemy", "alembic", "psycopg", "httpx", "pandas", "numpy", "pytest"]
HOSTS = {
    "NSE archives (official EOD)": "https://archives.nseindia.com/content/equities/EQUITY_L.csv",
    "Yahoo Finance (unofficial, delayed)": "https://query2.finance.yahoo.com/v8/finance/chart/%5ENSEI?range=1d",
    "Kite Connect (licensed)": "https://api.kite.trade/",
    "Anthropic API (optional AI)": "https://api.anthropic.com/",
}


def line(ok: bool | None, label: str, detail: str = "") -> None:
    mark = {True: "OK  ", False: "FAIL", None: "WARN"}[ok]
    print(f"[{mark}] {label}{': ' + detail if detail else ''}")


def main() -> int:
    critical_fail = False
    v = sys.version_info
    line(v >= (3, 11), "Python", f"{v.major}.{v.minor}.{v.micro}")
    for p in PACKAGES:
        try:
            m = importlib.import_module(p)
            line(True, f"package {p}", getattr(m, "__version__", ""))
        except ImportError:
            line(False, f"package {p}", "missing (pip install -r backend/requirements.txt)")
            critical_fail = True
    for tool in ("node", "npm", "psql", "docker"):
        path = shutil.which(tool)
        line(bool(path) or None, f"tool {tool}", path or "not found")
    try:
        with socket.create_connection(("localhost", 5432), timeout=2):
            line(True, "PostgreSQL on localhost:5432")
    except OSError:
        line(False, "PostgreSQL on localhost:5432", "not listening (start it or use Docker)")
        critical_fail = True
    if shutil.which("docker"):
        r = subprocess.run(["docker", "info"], capture_output=True, text=True)
        line(r.returncode == 0 or None, "Docker daemon", "running" if r.returncode == 0 else "not available (compose cannot run here)")
    for label, url in HOSTS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            code = urllib.request.urlopen(req, timeout=10).status
            line(True, label, f"HTTP {code}")
        except urllib.error.HTTPError as e:
            line(True if e.code in (401, 403, 404, 429) and "kite" not in url else None, label, f"reachable, HTTP {e.code}")
        except Exception as e:  # noqa: BLE001
            line(None, label, f"unreachable ({e.__class__.__name__})")
    print("\nCritical checks", "FAILED" if critical_fail else "passed")
    return 1 if critical_fail else 0


if __name__ == "__main__":
    sys.exit(main())
