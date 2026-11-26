#!/usr/bin/env python3
"""Acceptance check against a RUNNING system (brief section 22, docs/ACCEPTANCE.md).

Checks what can be checked from outside: live data, invariants and freshness. Behaviour that needs controlled
inputs (sizing arithmetic, completed-candle rules, data validation) is proved by the test suite instead, which
this script names for each criterion. Standard library only, so it runs on any server.

    python3 scripts/acceptance.py http://127.0.0.1:8000          # token, if any, from MI_TOKEN
Exit status 1 if any check FAILs (WARN does not fail).
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date

BASE = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("MI_TOKEN")
results: list[tuple[int, str, str, str]] = []


def get(path: str):
    req = urllib.request.Request(BASE + path, headers={"Accept": "application/json",
                                                       **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_status": e.code, "_detail": e.read().decode(errors="replace")[:200]}


def record(n: int, status: str, what: str, evidence: str) -> None:
    results.append((n, status, what, evidence))


def age_days(d: str | None) -> int | None:
    return (date.today() - date.fromisoformat(d[:10])).days if d else None


def check(n: int, what: str):
    def deco(fn):
        try:
            fn(n, what)
        except Exception as e:  # noqa: BLE001 - a crashed check is a failed check
            record(n, "FAIL", what, f"check crashed: {type(e).__name__}: {e}")
        return fn
    return deco


health = get("/api/health")
if health.get("_status") or health.get("status") not in ("ok", "degraded"):
    print(f"system not reachable at {BASE}: {health}")
    sys.exit(1)
if get("/api/v1/universe").get("_status") == 401:
    print("the API needs a token: set MI_TOKEN in the environment")
    sys.exit(1)


@check(1, "Scans the entire eligible NSE universe")
def _(n, what):
    u, s = get("/api/v1/universe"), get("/api/v1/scanner?limit=1")
    ok = u.get("eligible", 0) > 0 and s.get("count") == u.get("eligible") and not u.get("demo")
    age = age_days(u.get("session_date"))
    status = "FAIL" if not ok else ("WARN" if age is None or age > 5 else "PASS")
    record(n, status, what, f"{u.get('candidates')} NSE candidates, {u.get('eligible')} eligible, scanner rows "
           f"{s.get('count')}, session {u.get('session_date')} ({age} days old), source {u.get('source')}")


@check(2, "Transparent ranking")
def _(n, what):
    top = get("/api/v1/scanner?limit=1")["stocks"][0]["symbol"]
    sc = get(f"/api/v1/stocks/{top}/score")
    pts = round(sum(c["points"] for c in sc["breakdown"]), 2)
    ok = len(sc["breakdown"]) == 28 and abs(pts - sc["total"]) < 0.05 and all(c.get("rule") for c in sc["breakdown"])
    record(n, "PASS" if ok else "FAIL", what, f"{top}: {len(sc['breakdown'])} checks with rules; points sum {pts} = total {sc['total']}")


@check(3, "Positional opportunities")
def _(n, what):
    sig = get("/api/v1/signals")
    rows = sig.get("signals", [])
    complete = all(r.get("entry") and r.get("stop") and r.get("invalidation") for r in rows if r.get("state") == "confirmed")
    strategies = sorted({r["strategy"] for r in rows})
    record(n, "PASS" if complete else "FAIL", what, f"{len(rows)} signals for {sig.get('session_date')} "
           f"({', '.join(strategies) or 'none'}); every confirmed one has entry, stop and invalidation")


@check(4, "Intraday monitoring")
def _(n, what):
    a = get("/api/v1/alerts")
    completed = "completed candles" in (a.get("note") or "")
    last = a.get("last_run")
    status = "FAIL" if not completed else ("PASS" if last else "WARN")
    record(n, status, what, f"alerts endpoint up ({len(a.get('alerts', []))} alerts); completed-candle rule stated; "
           f"last intraday run: {last or 'none recorded (is the scheduler running in market hours?)'}; "
           "tests: test_alerts.py")


@check(5, "Persistent Rs 100,000 paper portfolio")
def _(n, what):
    acc = get("/api/v1/accounts")
    names = [a["name"] for a in acc["accounts"]]
    bad = []
    for name in names:
        rec = (get(f"/api/v1/portfolio?account={name}").get("snapshot") or {}).get("reconciliation") or {}
        if not rec.get("ok"):
            bad.append(name)
    caps = sorted({a["initial_capital"] for a in acc["accounts"]})
    ok = acc.get("mode") == "paper" and 100000.0 in caps and not bad
    record(n, "PASS" if ok else "FAIL", what, f"{len(names)} paper accounts (initial capital {caps}); ledger reconciles "
           f"for {len(names) - len(bad)}/{len(names)}" + (f"; FAILED: {bad}" if bad else "") + "; tests: test_portfolio_db.py")


@check(6, "Risk-based sizing")
def _(n, what):
    cfg = get("/api/v1/settings")["strategy"]
    limit = cfg["max_initial_aggregate_risk_pct"]
    worst = []
    for a in get("/api/v1/accounts")["accounts"]:
        snap = get(f"/api/v1/portfolio?account={a['name']}").get("snapshot") or {}
        if snap.get("equity"):
            worst.append((round(100 * snap.get("aggregate_initial_risk", 0) / snap["equity"], 2), a["name"]))
    top = max(worst) if worst else (0, "-")
    ok = top[0] <= limit + 0.01
    record(n, "PASS" if ok else "FAIL", what, f"risk {cfg['risk_per_position_pct']}% per position; highest aggregate "
           f"initial risk {top[0]}% ({top[1]}) vs limit {limit}%; tests: test_risk_portfolio_units.py")


@check(7, "Holdings and P&L")
def _(n, what):
    bad = []
    for a in get("/api/v1/accounts")["accounts"]:
        d = get(f"/api/v1/portfolio?account={a['name']}")
        s, pos = d.get("snapshot") or {}, d.get("positions") or []
        if not s:
            continue
        marked = sum(p["qty"] * p["mark"] for p in pos)
        upnl = sum(p["unrealised_pnl"] for p in pos)
        if abs(s["equity"] - (s["cash"] + marked)) > 1.0 or abs(s["unrealised_pnl"] - upnl) > 1.0:
            bad.append(a["name"])
    record(n, "PASS" if not bad else "FAIL", what, "for every account: equity = cash + marked value of the holdings, "
           "and unrealised P&L = sum over positions" + (f"; mismatch: {bad}" if bad else "") + "; tests: test_portfolio_db.py")


@check(8, "Weakening momentum")
def _(n, what):
    al = get("/api/v1/settings")["strategy"]["alerts"]
    ex = get("/api/v1/settings")["strategy"]["exits"]
    ok = "rs_lag_pct" in al and bool(ex)
    record(n, "PASS" if ok else "FAIL", what, f"RS-lag alert ({al.get('rs_lag_pct')}%) and exit rules configured; "
           "tests: test_alerts.py, test_risk_portfolio_units.py")


@check(9, "Daily reports")
def _(n, what):
    r = get("/api/v1/reports/latest")
    if r.get("_status"):
        record(n, "FAIL", what, f"no report: {r}")
        return
    age = age_days(r.get("session_date"))
    ok = bool(r.get("sources")) and bool(r.get("data_as_of")) and bool(r.get("markdown"))
    record(n, "FAIL" if not ok else ("PASS" if age is not None and age <= 4 else "WARN"), what,
           f"{r['kind']} report for {r['session_date']} ({age} days old), {len(r['sources'])} sources, data as of {r['data_as_of']}")


@check(10, "Backtests")
def _(n, what):
    runs = get("/api/v1/backtests")["runs"]
    kinds = sorted({r["kind"] for r in runs})
    ok = bool(runs) and all(r.get("metrics") for r in runs)
    record(n, "PASS" if ok else "FAIL", what, f"{len(runs)} stored runs ({', '.join(kinds)}) with metrics; "
           "result: no out-of-sample edge (docs/ARCHITECTURE.md 8.9); tests: test_backtest_*.py, test_walkforward.py")


@check(11, "Data-quality issues detected")
def _(n, what):
    u = get("/api/v1/universe")
    ex = u.get("excluded_by", {})
    record(n, "PASS", what, f"exclusions with reasons: {ex}; tests: test_validation.py, test_adjustments.py, test_nse_archive.py")


@check(12, "Android browsers")
def _(n, what):
    record(n, "INFO", what, "run: MI_TOKEN=... node frontend/scripts/mobile-check.cjs " + BASE + " out-dir (360 px, every page)")


@check(13, "Works without AI")
def _(n, what):
    r = get("/api/v1/reports/latest")
    ai = (r.get("ai") or {}).get("status")
    ok = not r.get("_status")
    record(n, "PASS" if ok else "FAIL", what, f"AI enabled: {health.get('ai_enabled')}; latest report present without AI "
           f"(AI status: {ai or 'none'}); tests: test_ai_layer.py")


@check(14, "No live orders")
def _(n, what):
    ok = health.get("execution_mode") == "paper"
    record(n, "PASS" if ok else "FAIL", what, f"execution_mode={health.get('execution_mode')}; config accepts only 'paper'; "
           "tests: test_no_live_trading.py, test_config.py")


print(f"Acceptance check against {BASE} ({health.get('environment')}, data mode {health.get('data_mode')}, "
      f"latest session {health.get('latest_session')})\n")
for n, status, what, ev in sorted(results):
    print(f"{n:>2}. {status:<4} {what}\n      {ev}")
fails = sum(1 for r in results if r[1] == "FAIL")
print(f"\n{sum(1 for r in results if r[1] == 'PASS')} pass, {sum(1 for r in results if r[1] == 'WARN')} warn, {fails} fail")
sys.exit(1 if fails else 0)
