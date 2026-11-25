#!/usr/bin/env python3
"""Today's ready-to-trade list across all four cap segments, ranked by backtest evidence.

    python ready_list.py --capital 500000 --risk-pct 0.75

Reads the newest momentum report CSV of each segment and the newest
``backtest.py --ready`` result, so run those first. Nothing places orders.
"""

import argparse
import glob
import html
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from scanner.momentum_report import CSS

ROOT = Path(__file__).resolve().parent
E = html.escape
SEGMENTS = {"nifty100": "Large", "midcap150": "Mid", "smallcap250": "Small", "microcap250": "Micro"}


def tier(row) -> tuple[int, str]:
    uni, n, own = row.get("UniR", np.nan), row.get("SameN", 0), row.get("SameR", np.nan)
    if pd.isna(uni) or uni < 0.05:
        return 3, "C: setup has no proven edge across the Nifty 500"
    if n >= 2 and own < 0:
        return 3, "C: setup has lost money on this stock before"
    net = row.get("SameNet", np.nan)
    if n >= 5 and own >= 0.2 and not pd.isna(net) and net > 0:
        return 1, "A: setup works across the Nifty 500 and on this stock"
    return 2, "B: setup works across the Nifty 500; little stock-specific history"


def main() -> None:
    p = argparse.ArgumentParser(description="Consolidated ready-to-trade list ranked by evidence.")
    p.add_argument("--capital", type=float, default=1_000_000)
    p.add_argument("--risk-pct", type=float, default=0.75)
    p.add_argument("--holdings", default="", help="Optional portfolio JSON, to mark stocks you already own")
    a = p.parse_args()
    frames = []
    for uni, seg in SEGMENTS.items():
        files = sorted(glob.glob(str(ROOT / "output" / f"momentum_{uni}_*.csv")), key=lambda f: Path(f).stat().st_mtime)
        if files:
            d = pd.read_csv(files[-1])
            d = d[d["Watchlist"] == "READY TO TRADE"].assign(Segment=seg, AsOf=Path(files[-1]).stem.split("_")[2])
            frames.append(d)
    ready = pd.concat(frames, ignore_index=True)
    bt = sorted(glob.glob(str(ROOT / "output" / "backtest_ready_*[0-9].csv")), key=lambda f: Path(f).stat().st_mtime)
    if bt:
        ev = pd.read_csv(bt[-1]).rename(columns={"Stock": "Symbol"})
        keep = ["Symbol", "SameN", "SameR", "SameNet", "UniR", "Same setup on this stock", "Same setup, Nifty 500, similar conditions", "Verdict"]
        ready = ready.merge(ev[[c for c in keep if c in ev.columns]], on="Symbol", how="left")
    held = set()
    if a.holdings and Path(a.holdings).exists():
        import json
        held = {h["symbol"] for h in json.loads(Path(a.holdings).read_text())["holdings"]}
    t = ready.apply(lambda r: tier(r), axis=1)
    ready["TierN"], ready["Tier"] = [x[0] for x in t], [x[1] for x in t]
    budget = a.capital * a.risk_pct / 100
    ready["Risk/share"] = ready["Entry"] - ready["Stop"]
    ready["Shares"] = (budget // ready["Risk/share"]).clip(lower=0).astype(int)
    ready["Capital"] = ready["Shares"] * ready["Entry"]
    ready["Held"] = ready["Symbol"].isin(held)
    ready = ready.sort_values(["TierN", "Score"], ascending=[True, False]).reset_index(drop=True)

    as_of = ready["AsOf"].max()
    lines = [f"# Ready to trade, {as_of[:4]}-{as_of[4:6]}-{as_of[6:]} close",
             f"Ranked by backtest evidence, then score. Sizing: {a.risk_pct}% of {a.capital:,.0f} = {budget:,.0f} risk per trade.", "",
             "| # | Stock | Seg | Score | Setup | Entry zone | Stop | T1 | T2 | Shares | Capital | Evidence | Own history | Nifty 500, similar conditions |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for i, r in ready.iterrows():
        lines.append(f"| {i + 1} | {r.Symbol}{' (held)' if r.Held else ''} | {r.Segment} | {r.Score:.0f} | {r.Setup} | {r.EntryLow:.2f}-{r.EntryHigh:.2f} | "
                     f"{r.Stop:.2f} | {r.T1:.2f} | {r.T2:.2f} | {r.Shares} | {r.Capital:,.0f} | {r.Tier.split(':')[0]} | "
                     f"{r.get('Same setup on this stock', 'N/A')} | {r.get('Same setup, Nifty 500, similar conditions', 'N/A')} |")
    lines += ["", "Evidence tiers: " + "; ".join(sorted(set(ready["Tier"]))) + ".",
              "Research only. Buy only if the next open is inside the entry zone; exit at the stop. No orders are placed."]
    stem = ROOT / "output" / f"ready_list_{as_of}_{datetime.now():%H%M}"
    Path(f"{stem}.md").write_text("\n".join(lines), encoding="utf-8")
    cls = {1: "on", 2: "neutral", 3: "off"}
    h = [f"<!doctype html><html lang=en><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<title>Ready To Trade</title><style>{CSS}</style></head><body><main><h1>Ready to trade</h1>",
         f"<p class=muted>{E(lines[1])}</p><div class=wrap><table><tr><th>#</th><th>Stock</th><th>Score</th><th>Setup</th><th>Entry zone</th>"
         "<th>Stop</th><th>T1</th><th>T2</th><th>Shares</th><th>Capital</th><th>Evidence</th><th>Own history</th><th>Nifty 500, similar</th></tr>"]
    for i, r in ready.iterrows():
        h.append(f"<tr><td class=n>{i + 1}</td><td><b>{E(r.Symbol)}</b>{' <span class=muted>(held)</span>' if r.Held else ''}<br>"
                 f"<span class=muted>{E(r.Segment)} · {E(r.Sector)}</span></td><td class=n>{r.Score:.0f}</td><td>{E(r.Setup)}</td>"
                 f"<td class=n>{r.EntryLow:.2f}–{r.EntryHigh:.2f}</td><td class=n>{r.Stop:.2f}</td><td class=n>{r.T1:.2f}</td>"
                 f"<td class=n>{r.T2:.2f}</td><td class=n>{r.Shares}</td><td class=n>{r.Capital:,.0f}</td>"
                 f"<td><span class='pill {cls[r.TierN]}'>{E(r.Tier.split(':')[0])}</span><br><span class=muted>{E(r.Tier.split(': ')[1])}</span></td>"
                 f"<td>{E(str(r.get('Same setup on this stock', 'N/A')))}</td><td>{E(str(r.get('Same setup, Nifty 500, similar conditions', 'N/A')))}</td></tr>")
    h.append(f"</table></div><footer>{E(lines[-1])}</footer></main></body></html>")
    Path(f"{stem}.html").write_text("".join(h), encoding="utf-8")
    print("\n".join(lines))
    print(f"\nReports:\n  {stem}.html\n  {stem}.md")


if __name__ == "__main__":
    main()
