#!/usr/bin/env python3
"""Review broker holdings with the momentum engine.

    python portfolio_review.py portfolio/holdings.json

The JSON file holds {"cash": ..., "holdings": [{"symbol", "qty", "avg_price"}]}.
Keep it in the git-ignored portfolio/ folder. Nothing here places orders.
"""

import argparse
import html
import logging
from datetime import datetime
from pathlib import Path

import numpy as np

from scanner.momentum import WEIGHTS
from scanner.momentum_report import CSS
from scanner.portfolio import review

E = html.escape
ORDER = {"EXIT SIGNAL": 0, "WEAK: REVIEW": 1, "HOLD: TIGHTEN STOP": 2, "HOLD": 3, "STRONG: HOLD": 4, "NO DATA": 5}
CLS = {"EXIT SIGNAL": "off", "WEAK: REVIEW": "off", "HOLD: TIGHTEN STOP": "neutral", "HOLD": "on", "STRONG: HOLD": "on", "NO DATA": "neutral"}


def _f(x, fmt="{:.1f}"):
    return "N/A" if x is None or (isinstance(x, float) and np.isnan(x)) else fmt.format(x)


def render(res: dict) -> tuple[str, str]:
    rows = sorted(res["rows"], key=lambda r: (ORDER.get(r["Signal"], 9), -r.get("Value", 0)))
    eq, rg = res["equity"], res["regime"]
    risk_cap = eq * res["max_open_risk_pct"] / 100
    per_trade = eq * rg.risk_per_trade_pct / 100
    md = [f"# Portfolio review: {res['as_of']:%d %b %Y} close",
          f"Source: {res['source']}. Generated {datetime.now():%d %b %Y %H:%M}. Research only; no orders are placed.", "",
          "## Account",
          f"- Account value {eq:,.0f}: invested {res['invested']:,.0f}, cash {res['cash']:,.0f}",
          f"- Unrealised P&L {res['pnl']:+,.0f} ({res['pnl'] / max(res['invested'] - res['pnl'], 1) * 100:+.1f}% on cost)",
          f"- If every trailing stop is hit: {res['open_risk']:,.0f} lost from today's prices ({res['open_risk'] / eq * 100:.1f}% of the account); "
          f"{res['loss_at_stops']:,.0f} below your cost ({res['loss_at_stops'] / eq * 100:.1f}%). Open-risk cap {res['max_open_risk_pct']}% = {risk_cap:,.0f}",
          f"- At {rg.risk_per_trade_pct}% risk per trade the cap allows about {int(res['max_open_risk_pct'] // rg.risk_per_trade_pct)} positions; you hold {sum(1 for r in res['rows'] if 'Value' in r)}",
          f"- Market regime {rg.label} ({rg.score:.1f}/100). Per-trade risk at {rg.risk_per_trade_pct}% = about {per_trade:,.0f} for this account",
          "- Sector weights: " + ", ".join(f"{k} {v:.0f}%" for k, v in res["sector_weights"].items()), "",
          "## Holdings",
          "| Stock | Signal | Qty | Avg | Close | P&L % | Weight | Score | Trailing stop | Risk to stop |",
          "|---|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if "Value" not in r:
            md.append(f"| {r['Symbol']} | {r['Signal']} | {r['Qty']:.0f} | {r['Avg']:.2f} | | | | | | |")
            continue
        md.append(f"| {r['Symbol']} | {r['Signal']} | {r['Qty']:.0f} | {r['Avg']:.2f} | {r['Close']:.2f} | {r['PnL%']:+.1f}% | "
                  f"{r['Weight%']:.0f}% | {r['Score']:.0f} | {r['Stop']:.2f} | {r['RiskToStop']:,.0f} ({r['Risk%Acct']:.1f}%) |")
    md.append("")
    for r in rows:
        if "Value" not in r:
            continue
        name = f", {r['Name']}" if r["Name"] else ""
        md.append(f"### {r['Symbol']}{name} ({r['Sector']}): {r['Signal']}")
        if r["Note"]:
            md.append(f"Note: {r['Note']}.")
        md.append(f"Close {r['Close']:.2f} ({r['Chg%']:+.1f}% on the day), cost {r['Avg']:.2f}, P&L {r['PnL']:+,.0f} ({r['PnL%']:+.1f}%). "
                  f"Score {r['Score']:.0f} ({r['Class']}); " + ", ".join(f"{k} {_f(v)}/{WEIGHTS[k]}" for k, v in r["Cats"].items()) + ".")
        md.append(f"- **Trend:** 20 EMA {r['EMA20']:.2f}, 50 EMA {r['EMA50']:.2f}, 200 EMA {_f(r['EMA200'], '{:.2f}')}; "
                  f"RSI {r['RSI']:.0f}, ADX {r['ADX']:.0f}; weekly trend {({True: 'confirmed', False: 'not confirmed', None: 'N/A'})[r['Weekly']]}.")
        md.append(f"- **Relative strength:** RS rating {_f(r['RSRating'], '{:.0f}')} of 99; 3-month return vs Nifty 500 {_f(r['RS3M'], '{:+.1f}')}%.")
        has_dv = r["Deliv"] is not None and not (isinstance(r["Deliv"], float) and np.isnan(r["Deliv"]))
        dv = f"delivery {r['Deliv']:.0f}% vs {_f(r['Deliv20'], '{:.0f}')}% average" if has_dv else "delivery N/A (not in NSE's EQ series)"
        md.append(f"- **Volume:** {r['RVOL']:.1f}x the 20-day average; {dv}.")
        md.append(f"- **Fundamentals:** " + ("; ".join(r["FundEvidence"]) or "N/A") + ".")
        md.append(f"- **Trailing stop:** {r['Stop']:.2f}, {r['StopWhy']}; {r['StopVsCost%']:+.1f}% versus your cost. "
                  f"Risk to stop {r['RiskToStop']:,.0f} ({r['Risk%Acct']:.1f}% of the account).")
        md.append(f"- **Setup now:** {r['Setup']}" + (f" ({r['Pattern']})" if r["Pattern"] else "") + f". Adding: {r['AddTrigger']}")
        if r["Reasons"]:
            md.append("- **Concerns:** " + " ".join(x if x.endswith(".") else x + "." for x in r["Reasons"]))
        if r["Events"]:
            md.append("- **Events:** " + " ".join(r["Events"]))
        md.append("")
    md.append("Signals are research output from end-of-day data, not instructions. Exchange announcements, pledging and the ASM list are not available to this tool; check them on NSE.")
    text = "\n".join(md)

    h = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>Portfolio Review {res['as_of']:%d %b %Y}</title><style>{CSS}</style></head><body><main>",
         f"<h1>Portfolio Review</h1><div class=muted>{E(res['source'])} · close of {res['as_of']:%d %b %Y} · research only, no orders placed</div>",
         "<div class=grid style='margin-top:16px'>",
         f"<div class=kv><span>Account value</span><b>{eq:,.0f}</b><span>cash {res['cash']:,.0f}</span></div>",
         f"<div class=kv><span>Unrealised P&amp;L</span><b class={'on' if res['pnl'] >= 0 else 'off'}>{res['pnl']:+,.0f}</b><span>on {res['invested'] - res['pnl']:,.0f} cost</span></div>",
         f"<div class=kv><span>Lost if all stops hit</span><b class={'off' if res['open_risk'] > risk_cap else 'on'}>{res['open_risk'] / eq * 100:.1f}%</b><span>{res['loss_at_stops'] / eq * 100:.1f}% below cost · cap {res['max_open_risk_pct']}%</span></div>",
         f"<div class=kv><span>Market regime</span><b class={CLS.get('HOLD') if rg.label == 'RISK-ON' else 'neutral'}>{rg.label}</b><span>{rg.score:.1f}/100</span></div>",
         "</div>",
         "<h2>Holdings</h2><div class=wrap><table><tr><th>Stock</th><th>Signal</th><th>Qty</th><th>Avg</th><th>Close</th><th>P&amp;L</th><th>Weight</th><th>Score</th><th>Trailing stop</th><th>Risk to stop</th></tr>"]
    for r in rows:
        if "Value" not in r:
            continue
        h.append(f"<tr><td><b>{E(r['Symbol'])}</b><br><span class=muted>{E(r['Sector'])}</span></td><td><span class='pill {CLS[r['Signal']]}'>{E(r['Signal'])}</span></td>"
                 f"<td class=n>{r['Qty']:.0f}</td><td class=n>{r['Avg']:.2f}</td><td class=n>{r['Close']:.2f}</td>"
                 f"<td class=n>{r['PnL']:+,.0f}<br><span class=muted>{r['PnL%']:+.1f}%</span></td><td class=n>{r['Weight%']:.0f}%</td>"
                 f"<td class=n>{r['Score']:.0f}</td><td class=n>{r['Stop']:.2f}</td><td class=n>{r['RiskToStop']:,.0f}<br><span class=muted>{r['Risk%Acct']:.1f}%</span></td></tr>")
    h.append("</table></div>")
    for line in text.split("\n"):
        if line.startswith("### "):
            h.append(f"<div class=card><h3>{E(line[4:])}</h3>")
        elif line.startswith("- ") and h and not h[-1].endswith("</ul>"):
            pass
        if line.startswith("- **"):
            k, v = line[4:].split(":**", 1)
            h.append(f"<div><b>{E(k)}:</b>{E(v)}</div>")
        elif line.startswith("Close ") or line.startswith("Note:"):
            h.append(f"<div class=muted>{E(line)}</div>")
        elif line == "" and h and "class=card" in "".join(h[-12:]) and not h[-1].endswith("</div></div>"):
            if any("class=card" in x for x in h[-12:]):
                h.append("</div>")
    h.append(f"<h2>Sector weights</h2><p>{E(', '.join(f'{k} {v:.0f}%' for k, v in res['sector_weights'].items()))}</p>")
    h.append("<footer>Signals are research output from end-of-day data, not instructions. Exchange announcements, pledging and the ASM list are not available to this tool; check them on NSE before acting.</footer></main></body></html>")
    return text, "".join(h)


def main() -> None:
    p = argparse.ArgumentParser(description="Momentum review of broker holdings (read-only).")
    p.add_argument("holdings", help="JSON file with cash and holdings")
    p.add_argument("--risk-pct", type=float, default=0.75)
    p.add_argument("--max-open-risk", type=float, default=3.0)
    p.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "portfolio"))
    a = p.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    for noisy in ("yfinance", "peewee", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.CRITICAL)
    res = review(a.holdings, a.risk_pct, a.max_open_risk)
    text, page = render(res)
    out = Path(a.output_dir)
    out.mkdir(parents=True, exist_ok=True)
    stem = f"review_{res['as_of']:%Y%m%d}_{datetime.now():%H%M}"
    (out / f"{stem}.md").write_text(text, encoding="utf-8")
    (out / f"{stem}.html").write_text(page, encoding="utf-8")
    print(text)
    print(f"\nReports:\n  {out / (stem + '.md')}\n  {out / (stem + '.html')}")


if __name__ == "__main__":
    main()
