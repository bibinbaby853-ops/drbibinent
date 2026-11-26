"""Render a ScanResult as Markdown and as a self-contained HTML page."""

from __future__ import annotations

import html
from pathlib import Path

import numpy as np
import pandas as pd

from .engine import ScanResult
from .momentum import WEIGHTS
from .tradeplan import BREAKOUT, PULLBACK, READY

E = html.escape


def _f(x, fmt="{:.2f}", na="N/A") -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return na
    return fmt.format(x)


_HIST_CACHE: dict[str, dict | None] = {}


def pattern_history(universe: str) -> dict | None:
    """Backtest statistics written by backtest.py, for this universe or else the Nifty 500 run."""
    import json

    key = Path(universe).stem
    if key not in _HIST_CACHE:
        base = Path(__file__).resolve().parent.parent / "history"
        found = None
        for name in (key, "nifty500"):
            f = base / f"pattern_stats_{name}.json"
            if f.exists():
                found = json.loads(f.read_text())
                break
        _HIST_CACHE[key] = found
    return _HIST_CACHE[key]


def history_note(r: dict, universe: str, short: bool = False) -> str:
    h = pattern_history(universe)
    if not h or not r["Patterns"]:
        return "N/A" if short else ""
    st = h["patterns"].get(r["Patterns"][0].name)
    if not st:
        return "N/A" if short else ""
    if short:
        return f"{st['win_pct']:.0f}% ({st['avg_r']:+.2f}R, n={st['trades']})"
    base = h.get("baseline_uptrend_avg_r", h.get("baseline_avg_r"))
    return (f"Backtest ({h['universe']}, {h['first_entry'][:4]}-{h['last_entry'][:4]}): this pattern reached target 1 in "
            f"{st['win_pct']:.0f}% of {st['trades']} trades, averaging {st['avg_r']:+.2f}R per trade"
            + (f", versus {base:+.2f}R for random entries in uptrending stocks" if base is not None else "") + ".")


def _cap(s: str) -> str:
    return s[:1].upper() + s[1:] if s else s


def _pct(x) -> str:
    return _f(x, "{:+.1f}%")


# --------------------------------------------------------------------------- #
# Per-stock narrative (the nine points)
# --------------------------------------------------------------------------- #

def stock_sections(r: dict, universe: str = "nifty500") -> list[tuple[str, str]]:
    cats, d = r["CatObjs"], r["Daily"]
    rs, rs5 = r["RS"], r["RS500"]
    out = []

    why = []
    for k in ("Trend", "Relative strength", "Momentum"):
        why += cats[k].evidence[:3]
    out.append(("Why it is showing momentum", _cap("; ".join(why)) or "N/A"))

    tech = []
    if r["Patterns"]:
        p = r["Patterns"][0]
        tech.append(f"{p.name} ({p.stage}): {p.detail}")
    tech.append(f"close {d.Close:.2f} vs 20 EMA {d.EMA20:.2f}, 50 EMA {d.EMA50:.2f}, 200 EMA {_f(d.EMA200)}")
    tech.append(f"RSI {d.RSI14:.0f}, ADX {d.ADX:.0f}, MACD histogram {d.Hist:+.2f}, Supertrend {'bullish' if d.ST_Dir == 1 else 'bearish'}")
    tech.append("weekly trend " + ({True: "confirmed", False: "not confirmed", None: "N/A"}[r["Weekly"]])
                + ", monthly trend " + ({True: "confirmed", False: "not confirmed", None: "N/A"}[r["Monthly"]]))
    hist = history_note(r, universe)
    out.append(("Key technical evidence", _cap("; ".join(tech)) + (f". {hist}" if hist else "")))

    rel = (f"vs Nifty 50: 1W {_pct(rs.get('1W'))}, 1M {_pct(rs.get('1M'))}, 3M {_pct(rs.get('3M'))}. "
           f"vs Nifty 500: 1W {_pct(rs5.get('1W'))}, 1M {_pct(rs5.get('1M'))}, 3M {_pct(rs5.get('3M'))}. "
           f"RS rating {_f(r['RSRating'], '{:.0f}')} of 99.")
    if r["Resilience"] is not None:
        rel += f" On index down days it averaged {r['Resilience']:+.2f}% versus the index."
    out.append(("Relative strength versus Nifty", rel))

    vol = [f"last session {d.RVOL:.1f}x the 20-day average"]
    dv = r["Delivery"]
    if dv and not pd.isna(dv.get("DelivPct")):
        vol.append(f"delivery {dv['DelivPct']:.0f}% vs {_f(dv.get('DelivPct20'), '{:.0f}')}% 20-day average")
    else:
        vol.append("delivery N/A")
    vol += [e for e in cats["Volume"].evidence if not e.startswith("volume ") and not e.startswith("delivery")]
    out.append(("Volume confirmation", _cap("; ".join(vol))))

    sr = r["SectorRow"]
    if sr is not None:
        out.append(("Sector outlook", f"{r['Sector']} ranks {int(sr['Rank'])} of the sectors scanned "
                    f"(1M relative return {sr['RS1M']:+.1f}%, 3M {sr['RS3M']:+.1f}%), {sr['Rotation'].lower()}, "
                    f"{sr['Above50DMA%']:.0f}% of its stocks above the 50-day average."))
    else:
        out.append(("Sector outlook", "N/A"))

    trig = r["Trigger"]
    it = r["Intraday"]
    if it is not None:
        trig += f" Intraday ({it.last_bar:%d %b %H:%M}): {it.note_4h}; {it.note_1h}."
        if not it.confirm_4h:
            trig += " Wait for the 4-hour chart to turn up before entering."
    else:
        trig += " Intraday confirmation N/A."
    out.append(("Entry trigger", trig))

    out.append(("Stop-loss logic", f"Stop {r['Stop']:.2f}, {r['StopLogic']}. ATR-based alternative {r['ATRStop']:.2f}. "
                f"Technical invalidation: {r['Invalidation']}. Targets: {r['TargetNote']}."))

    avoid = list(r["Flags"])
    for k, c in cats.items():
        sc = c.score
        if sc is not None and sc < 0.5 * WEIGHTS[k]:
            avoid.append(f"Weak {k.lower()} score ({sc:.1f} of {WEIGHTS[k]}).")
        if sc is None:
            avoid.append(f"{k} data unavailable, so the score is normalised without it.")
    f = r["Fund"]
    out.append(("Reasons to avoid the trade", " ".join(avoid) if avoid else "No specific red flags beyond normal market risk."))

    ev = list(r["Events"])
    if f is not None and f.earnings_date and not ev:
        ev.append(f"Next results expected {f.earnings_date:%d %b %Y}.")
    if not ev:
        ev.append("Earnings date N/A.")
    ev.append("Corporate announcements N/A (exchange feed not reachable); check NSE filings before entry.")
    out.append(("Upcoming risk events", " ".join(ev)))
    return out


def fundamentals_line(r: dict) -> str:
    c = r["CatObjs"]["Fundamentals"]
    parts = c.evidence[:]
    f = r["Fund"]
    if f is not None:
        if f.promoter_pct is not None:
            parts.append(f"promoter/insider holding {f.promoter_pct:.1f}% (Yahoo approximation)")
        if f.institutional_pct is not None:
            parts.append(f"institutional holding {f.institutional_pct:.1f}%")
    parts.append("pledging N/A")
    return "; ".join(parts)


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #

def to_markdown(res: ScanResult) -> str:
    L = []
    rg = res.regime
    L.append(f"# Daily Momentum Report: data as of {res.as_of:%d %b %Y} (end of day)")
    L.append(f"Generated {res.generated:%d %b %Y %H:%M}. Universe {res.cfg.universe}: {res.scanned} of {res.universe_size} stocks passed data, liquidity and surveillance filters.")
    L.append("")
    L.append(f"## Market regime: {rg.label} ({rg.score:.1f}/100)")
    for n in rg.notes:
        L.append(f"- {n}")
    L.append(f"- Risk guidance: {rg.risk_per_trade_pct}% per trade, {rg.max_open_risk_pct}% maximum open risk, new entries need a score of {rg.min_score_ready:.0f}+.")
    L.append("")
    L.append("## Strongest sectors")
    L.append("| Rank | Sector | 1M vs median stock | 3M vs median stock | Above 50 DMA | Rotation | Leaders |")
    L.append("|---|---|---|---|---|---|---|")
    for sec, s in res.sectors.head(8).iterrows():
        L.append(f"| {int(s['Rank'])} | {sec} | {s['RS1M']:+.1f}% | {s['RS3M']:+.1f}% | {s['Above50DMA%']:.0f}% | {s['Rotation']} | {', '.join(res.sector_leaders.get(sec, []))} |")
    L.append("")
    L.append("## Top momentum candidates")
    L.append("| Rank | Stock | CMP | Score | Setup | Entry | Stop Loss | Target 1 | Target 2 | R:R | Pattern hit rate (5y) | List |")
    L.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(res.top, 1):
        L.append(f"| {i} | {r['Symbol']} | {r['CMP']:.2f} | {r['Score']:.0f} | {r['Setup']} | {r['EntryLow']:.2f}-{r['EntryHigh']:.2f} | {r['Stop']:.2f} | {r['T1']:.2f} | {r['T2']:.2f} | 1:{r['R:R']:.1f} | {history_note(r, res.cfg.universe, True)} | {r['Watchlist'].title()} |")
    L.append("")
    for i, r in enumerate(res.top, 1):
        L.append(f"### {i}. {r['Symbol']}, {r['Name']} ({r['Sector']})")
        L.append(f"Score {r['Score']:.0f} ({r['Class']}), data coverage {r['Coverage']}/100. List: {r['Watchlist'].title()}. Holding period {r['Holding']}.")
        cs = r["Cats"]
        L.append("Score breakdown: " + ", ".join(f"{k} {_f(v, '{:.1f}')}/{WEIGHTS[k]}" for k, v in cs.items()))
        L.append(f"Fundamentals: {fundamentals_line(r)}.")
        for n, (title, text) in enumerate(stock_sections(r, res.cfg.universe), 1):
            L.append(f"{n}. **{title}.** {text}")
        L.append("")
    for wl in (READY, BREAKOUT, PULLBACK):
        items = [r for r in res.rows if r["Watchlist"] == wl]
        L.append(f"## {wl.title()} ({len(items)})")
        if items:
            L.append("| Stock | Score | Change | Setup | CMP | Entry | Stop | T1 | R:R |")
            L.append("|---|---|---|---|---|---|---|---|---|")
            for r in items:
                L.append(f"| {r['Symbol']} | {r['Score']:.0f} | {_delta(r)} | {r['Setup']} | {r['CMP']:.2f} | {r['Entry']:.2f} | {r['Stop']:.2f} | {r['T1']:.2f} | 1:{r['R:R']:.1f} |")
        L.append("")
    L.append(f"## Failed breakouts ({len(res.failed)})")
    for f in res.failed:
        L.append(f"- {f['Symbol']}: {f['Detail']}")
    L.append("")
    L.append(f"## Removed since {res.prev_as_of or 'last run'} ({len(res.removed)})")
    for f in res.removed:
        L.append(f"- {f['Symbol']} (was {f['Was'].title()}, {f['PrevScore']:.0f}): {f['Reason']}")
    L.append("")
    L.append("## Position plan")
    L.append(_portfolio_text(res))
    L.append("")
    L.append("## Data integrity")
    L.append("| Data | Source | Timeliness |")
    L.append("|---|---|---|")
    for a, b, c in res.data_notes:
        L.append(f"| {a} | {b} | {c} |")
    L.append("")
    L.append("Excluded: " + "; ".join(f"{k}: {len(v)}" for k, v in res.excluded.items()))
    L.append("")
    L.append("_Research output only. Not investment advice. No orders are placed._")
    return "\n".join(L)


def _delta(r: dict) -> str:
    if r.get("Delta") is None:
        return r.get("Status", "")
    return f"{r['Delta']:+.0f}"


def _portfolio_text(res: ScanResult) -> str:
    rg = res.regime
    if not res.portfolio:
        return (f"No trade meets every READY condition today. With a {rg.label} market, preserving capital "
                f"is the better choice; work the breakout and pullback watchlists instead.")
    lines = [f"Capital {res.cfg.capital:,.0f}, {rg.risk_per_trade_pct}% risk per trade, open-risk cap {rg.max_open_risk_pct}%, at most 2 per sector:"]
    for r in res.portfolio:
        lines.append(f"- {r['Symbol']}: {r['Qty']} shares at about {r['Entry']:.2f}, stop {r['Stop']:.2f}, risk about {r['RiskAmt']:,.0f}")
    lines.append(f"Total open risk if all fill: {len(res.portfolio) * rg.risk_per_trade_pct:.2f}% of capital. Never average down; exit at the stop.")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# HTML
# --------------------------------------------------------------------------- #

CSS = """
:root{--bg:#fff;--fg:#0f172a;--muted:#64748b;--line:#e2e8f0;--card:#f8fafc;--accent:#1d4ed8;--good:#15803d;--warn:#b45309;--bad:#b91c1c}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#0b1120;--fg:#e2e8f0;--muted:#94a3b8;--line:#1e293b;--card:#111827;--accent:#60a5fa;--good:#4ade80;--warn:#fbbf24;--bad:#f87171}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:32px 0 10px;border-bottom:1px solid var(--line);padding-bottom:6px}
h3{font-size:16px;margin:0}.muted{color:var(--muted)}.wrap{overflow-x:auto}table{border-collapse:collapse;width:100%}
th,td{padding:7px 9px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-weight:600;white-space:nowrap;background:var(--card)}
td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}.pill{display:inline-block;padding:1px 8px;border-radius:999px;font-size:12px;font-weight:600;border:1px solid currentColor}
.on{color:var(--good)}.neutral{color:var(--warn)}.off{color:var(--bad)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:14px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}.kv{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:10px}
.kv b{display:block;font-size:18px}.kv span{color:var(--muted);font-size:12px}ol{padding-left:20px;margin:8px 0}ol li{margin:4px 0}
.bars{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0}.bar{font-size:12px;border:1px solid var(--line);border-radius:6px;padding:2px 6px}
.flag{color:var(--warn)}footer{margin-top:40px;font-size:12px;color:var(--muted)}
"""


def _tr(cells: list[str], num: set[int] = frozenset()) -> str:
    return "<tr>" + "".join(f'<td class="n">{c}</td>' if i in num else f"<td>{c}</td>" for i, c in enumerate(cells)) + "</tr>"


def to_html(res: ScanResult) -> str:
    rg = res.regime
    cls = {"RISK-ON": "on", "NEUTRAL": "neutral", "RISK-OFF": "off"}[rg.label]
    br = res.breadth
    h = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>Momentum Report {res.as_of:%d %b %Y}</title><style>{CSS}</style></head><body><main>"]
    h.append(f"<h1>Daily Momentum Report</h1><div class=muted>End-of-day data as of <b>{res.as_of:%d %b %Y}</b> · generated {res.generated:%d %b %Y %H:%M} · "
             f"{res.scanned} of {res.universe_size} {E(res.cfg.universe)} stocks passed filters</div>")
    n50, n500 = res.n50, res.n500
    h.append("<div class=grid style='margin-top:16px'>")
    h.append(f"<div class=kv><span>Market regime</span><b class={cls}>{rg.label}</b><span>{rg.score:.1f}/100</span></div>")
    for it in (n50, n500):
        if it:
            h.append(f"<div class=kv><span>{it.name}</span><b>{it.close:,.0f}</b><span>{it.label} · 1M {it.ret_1m:+.1f}%</span></div>")
    if br:
        h.append(f"<div class=kv><span>Above 50-day average</span><b>{br['pct_above_50']:.0f}%</b><span>{br['pct_above_200']:.0f}% above 200-day</span></div>")
        h.append(f"<div class=kv><span>Advance / decline</span><b>{br['advances']} / {br['declines']}</b><span>{br['new_highs']} highs · {br['new_lows']} lows</span></div>")
    h.append(f"<div class=kv><span>Risk per trade · open cap</span><b>{rg.risk_per_trade_pct}% · {rg.max_open_risk_pct}%</b><span>entries need score {rg.min_score_ready:.0f}+</span></div>")
    h.append("</div><div class=card><ul style='margin:0;padding-left:18px'>" + "".join(f"<li>{E(n)}</li>" for n in rg.notes) + "</ul></div>")

    h.append("<h2>Sector strength and rotation</h2><div class=wrap><table><tr><th>#</th><th>Sector</th><th>Stocks</th><th>1W vs median</th><th>1M vs median</th><th>3M vs median</th><th>Above 50 DMA</th><th>Rotation</th><th>RS leaders</th></tr>")
    for sec, s in res.sectors.iterrows():
        h.append(_tr([str(int(s["Rank"])), E(sec), str(int(s["Stocks"])), f"{s['RS1W']:+.1f}%", f"{s['RS1M']:+.1f}%", f"{s['RS3M']:+.1f}%",
                      f"{s['Above50DMA%']:.0f}%", E(s["Rotation"]), E(", ".join(res.sector_leaders.get(sec, [])))], {2, 3, 4, 5, 6}))
    h.append("</table></div>")

    h.append("<h2>Top momentum candidates</h2><div class=wrap><table><tr><th>Rank</th><th>Stock</th><th>CMP</th><th>Score</th><th>Setup</th><th>Entry</th><th>Stop Loss</th><th>Target 1</th><th>Target 2</th><th>R:R</th><th>Pattern hit rate (5y)</th><th>List</th></tr>")
    for i, r in enumerate(res.top, 1):
        h.append(_tr([str(i), f"<b>{E(r['Symbol'])}</b><br><span class=muted>{E(r['Sector'])}</span>", f"{r['CMP']:.2f}",
                      f"<b>{r['Score']:.0f}</b><br><span class=muted>{E(r['Class'])}</span>", E(r["Setup"]),
                      f"{r['EntryLow']:.2f}–{r['EntryHigh']:.2f}", f"{r['Stop']:.2f}<br><span class=muted>-{r['Risk%']:.1f}%</span>",
                      f"{r['T1']:.2f}", f"{r['T2']:.2f}", f"1:{r['R:R']:.1f}", E(history_note(r, res.cfg.universe, True)),
                      E(r["Watchlist"].title())], {0, 2, 3, 5, 6, 7, 8, 9}))
    h.append("</table></div>")

    for i, r in enumerate(res.top, 1):
        h.append(f"<div class=card><h3>{i}. {E(r['Symbol'])} <span class=muted>· {E(r['Name'])} · {E(r['Sector'])}</span></h3>")
        h.append(f"<div class=muted>Score <b>{r['Score']:.0f}</b> ({E(r['Class'])}) · coverage {r['Coverage']}/100 · {E(r['Watchlist'].title())} · "
                 f"CMP {r['CMP']:.2f} (EOD {res.as_of:%d %b}) · holding {E(r['Holding'])} · size {r['Qty']} shares, risk about {r['RiskAmt']:,.0f}</div>")
        h.append("<div class=bars>" + "".join(f"<span class=bar>{E(k)} {_f(v, '{:.1f}')}/{WEIGHTS[k]}</span>" for k, v in r["Cats"].items()) + "</div>")
        h.append(f"<div class=muted>Breakout entry {r['BreakoutEntry']:.2f} · pullback entry {r['PullbackEntry']:.2f} · structure stop {r['StructureStop']:.2f} · ATR stop {r['ATRStop']:.2f} · R:R to T2 1:{r['R:R2']:.1f}</div>")
        h.append(f"<div class=muted>Fundamentals: {E(fundamentals_line(r))}</div><ol>")
        for title, text in stock_sections(r, res.cfg.universe):
            cls_ = " class=flag" if title.startswith("Reasons") else ""
            h.append(f"<li{cls_}><b>{E(title)}.</b> {E(text)}</li>")
        h.append("</ol></div>")

    for wl in (READY, BREAKOUT, PULLBACK):
        items = [r for r in res.rows if r["Watchlist"] == wl]
        h.append(f"<h2>{E(wl.title())} <span class=muted>({len(items)})</span></h2>")
        if not items:
            h.append("<p class=muted>None today.</p>")
            continue
        h.append("<div class=wrap><table><tr><th>Stock</th><th>Score</th><th>Change</th><th>Setup</th><th>CMP</th><th>Entry zone</th><th>Stop</th><th>T1</th><th>T2</th><th>R:R</th><th>Flags</th></tr>")
        for r in items:
            h.append(_tr([f"<b>{E(r['Symbol'])}</b><br><span class=muted>{E(r['Sector'])}</span>", f"{r['Score']:.0f}", E(_delta(r)), E(r["Setup"]),
                          f"{r['CMP']:.2f}", f"{r['EntryLow']:.2f}–{r['EntryHigh']:.2f}", f"{r['Stop']:.2f}", f"{r['T1']:.2f}", f"{r['T2']:.2f}",
                          f"1:{r['R:R']:.1f}", "<span class=flag>" + E(" ".join(r["Flags"] + r["Events"])[:220]) + "</span>"], {1, 4, 5, 6, 7, 8, 9}))
        h.append("</table></div>")

    h.append(f"<h2>Failed breakouts <span class=muted>({len(res.failed)})</span></h2>")
    h.append("<ul>" + "".join(f"<li><b>{E(f['Symbol'])}</b> ({E(f['Sector'])}): {E(f['Detail'])}</li>" for f in res.failed) + "</ul>" if res.failed else "<p class=muted>None.</p>")
    h.append(f"<h2>Removed since {E(res.prev_as_of or 'last run')} <span class=muted>({len(res.removed)})</span></h2>")
    h.append("<ul>" + "".join(f"<li><b>{E(f['Symbol'])}</b> was {E(f['Was'].title())} at {f['PrevScore']:.0f}: {E(f['Reason'])}</li>" for f in res.removed) + "</ul>"
             if res.removed else f"<p class=muted>{'No earlier run to compare with.' if not res.prev_as_of else 'None.'}</p>")

    h.append("<h2>Position plan</h2><div class=card>" + "<br>".join(E(x) for x in _portfolio_text(res).split("\n")) + "</div>")
    if res.flagged:
        h.append("<h2>Trading restriction flags</h2><ul>" + "".join(f"<li><b>{E(f['Symbol'])}</b>: {E(f['Flag'])}</li>" for f in res.flagged) + "</ul>")
    h.append("<h2>Data integrity</h2><div class=wrap><table><tr><th>Data</th><th>Source</th><th>Timeliness</th></tr>")
    for a, b, c in res.data_notes:
        h.append(_tr([E(a), E(b), E(c)]))
    h.append("</table></div><p class=muted>Excluded: " + E("; ".join(f"{k}: {', '.join(v[:12])}{' …' if len(v) > 12 else ''}" for k, v in res.excluded.items())) + "</p>")
    h.append("<footer>Research output only. Not investment advice. The scanner never places orders. Chart patterns are detected heuristically; confirm every setup on a chart and check exchange filings before trading.</footer></main></body></html>")
    return "".join(h)


def write_reports(res: ScanResult, out_dir: Path) -> tuple[Path, Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"momentum_{Path(res.cfg.universe).stem}_{res.as_of:%Y%m%d}_{res.generated:%H%M}"
    hp, mp, cp = out_dir / f"{stem}.html", out_dir / f"{stem}.md", out_dir / f"{stem}.csv"
    hp.write_text(to_html(res), encoding="utf-8")
    mp.write_text(to_markdown(res), encoding="utf-8")
    flat = [{k: v for k, v in r.items() if k not in ("CatObjs", "Patterns", "Intraday", "Delivery", "Fund", "SectorRow", "Daily", "RS", "RS500", "Cats")}
            | {f"Cat_{k}": v for k, v in r["Cats"].items()} for r in res.rows]
    pd.DataFrame(flat).to_csv(cp, index=False)
    return hp, mp, cp
