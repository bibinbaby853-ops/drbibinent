"""Phase 6 unit tests: charges, slippage, ledger accounting, risk checks, exits."""

from datetime import date

import pytest

from app.config import CostConfig, ExitConfig, SlippageConfig, StrategyConfig
from app.portfolio.costs import charges, fill_price, slippage_bps
from app.portfolio.exits import after_close, intraday
from app.portfolio.ledger import Ledger, Position
from app.risk.engine import CHECKS, Context, OpenExposure, Proposal, evaluate

CFG = StrategyConfig()
POLICY = {"allow_new_entries": True, "max_new_entries_per_day": 3, "risk_multiplier": 1.0, "min_score": 65}
D = date(2026, 11, 23)


# ------------------------------------------------------------------ charges

def test_delivery_charges_for_one_lakh_buy_and_sell():
    b = charges("buy", 100, 1000.0, CostConfig())
    assert (b.stt, b.exchange_txn, b.sebi_fee, b.stamp_duty, b.dp_charge) == (100.0, 2.97, 0.1, 15.0, 0.0)
    assert b.gst == round((2.97 + 0.1) * 0.18, 2) and b.total == pytest.approx(118.62)
    s = charges("sell", 100, 1000.0, CostConfig())
    assert s.stamp_duty == 0 and s.dp_charge == 15.93 and s.total == pytest.approx(100 + 2.97 + 0.1 + 0.55 + 15.93)


def test_brokerage_is_capped_and_taxed():
    c = charges("buy", 100, 1000.0, CostConfig(brokerage_pct=0.03, brokerage_cap_inr=20))
    assert c.brokerage == 20 and c.gst == round((20 + 2.97 + 0.1) * 0.18, 2)


def test_slippage_by_liquidity_and_tick_rounding():
    s = SlippageConfig()
    assert slippage_bps(100, s) == 5 and slippage_bps(20, s) == 10 and slippage_bps(6, s) == 20 and slippage_bps(None, s) == 20
    assert fill_price("buy", 100.0, 10) == 100.10 and fill_price("sell", 100.0, 10) == 99.90
    assert fill_price("buy", 123.41, 0) == 123.40  # nearest 0.05


# ------------------------------------------------------------------ ledger

def pos(key="A", qty=100, stop=90.0, entry=100.0, sector="IT", stock_id=1):
    return Position(key=key, stock_id=stock_id, symbol=key, sector=sector, qty=qty, avg_price=entry, initial_stop=stop, stop=stop,
                    initial_risk=qty * (entry - stop), entry_date=D, strategy="manual", highest_close=entry, entry_qty=qty,
                    buy_charges=0.0)


def test_ledger_buy_partial_sell_and_reconcile():
    L = Ledger.new(100_000)
    L.buy(pos(), 100.0, 12.0)
    assert L.cash == pytest.approx(100_000 - 10_000 - 12)
    pnl = L.sell("A", 40, 110.0, 5.0)
    assert pnl == pytest.approx(40 * 10 - 5 - 12 * 0.4)
    L.mark({1: 120.0})
    assert L.equity == pytest.approx(L.cash + 60 * 120)
    L.sell("A", 60, 95.0, 4.0)
    assert "A" not in L.positions
    rec = L.reconcile()
    assert rec["ok"] and L.realised_pnl == pytest.approx(400 - 5 - 4.8 + (95 - 100) * 60 - 4 - 7.2)
    assert L.equity == pytest.approx(100_000 + L.realised_pnl)


def test_ledger_refuses_overspending_and_oversells():
    L = Ledger.new(1_000)
    with pytest.raises(ValueError, match="insufficient cash"):
        L.buy(pos(qty=100), 100.0, 1.0)
    L.buy(pos(qty=5), 100.0, 1.0)
    with pytest.raises(ValueError):
        L.sell("A", 6, 100.0, 0)


def test_split_while_held_keeps_value_and_risk():
    L = Ledger.new(100_000)
    L.buy(pos(qty=100, stop=90), 100.0, 0.0)
    L.adjust_for_corporate_action(1, 0.5)  # 1:2 split
    p = L.positions["A"]
    assert (p.qty, p.avg_price, p.stop, p.entry_qty) == (200, 50.0, 45.0, 200)
    assert p.qty * p.avg_price == 10_000 and p.risk_per_share == pytest.approx(5.0)
    assert L.reconcile()["ok"]  # value-neutral: cash and cost basis unchanged
    L.mark({1: 55.0})
    L.sell("A", 200, 55.0, 0.0)
    assert L.realised_pnl == pytest.approx(1_000) and L.reconcile()["ok"]


# ------------------------------------------------------------------ risk engine

def ctx(**kw):
    base = dict(equity=100_000, cash=100_000, exposures=[], eligible=True, median_value_cr=60, regime="strong_bullish",
                policy=POLICY, entries_today=0, atr=2.0, correlations={}, adverse_gap_pct=1.0)
    base.update(kw)
    return Context(**base)


def prop(**kw):
    base = dict(stock_id=9, symbol="NEW", sector="IT", strategy="manual", session_date=D, entry=100.0, stop=95.0, score=90)
    base.update(kw)
    return Proposal(**base)


def test_sizing_uses_half_percent_risk_at_expected_fill():
    d = evaluate(prop(), ctx(), CFG)
    assert d.approved and [c["check"] for c in d.checks] == list(CHECKS)
    # budget 500; fill 100.05 (5 bps); risk 5.05 -> 99 shares; position cap 20k/100.05 = 199
    assert d.qty == 99 and d.expected_fill == 100.05 and d.initial_risk == pytest.approx(99 * 5.05)


def test_regime_multiplier_and_policy_gates():
    half = evaluate(prop(), ctx(policy=dict(POLICY, risk_multiplier=0.5)), CFG)
    assert half.qty == 49
    for kw, why in [(dict(policy=dict(POLICY, allow_new_entries=False)), "does not allow"),
                    (dict(entries_today=3), "daily new-entry limit"),
                    (dict(eligible=False), "not eligible")]:
        d = evaluate(prop(), ctx(**kw), CFG)
        assert not d.approved and why in d.reason
        assert d.checks[-1]["outcome"] == "skipped"
    low = evaluate(prop(score=60), ctx(), CFG)
    assert not low.approved and "below" in low.reason


def test_tight_stop_is_sized_as_one_atr():
    d = evaluate(prop(stop=99.5), ctx(atr=4.0), CFG)
    assert d.qty == 125  # 500 / 4.0 (ATR), not 500 / 0.55


def test_invalid_and_too_wide_stops_are_rejected():
    assert "below the entry" in evaluate(prop(stop=101), ctx(), CFG).reason
    assert "exceeds 15" in evaluate(prop(stop=80), ctx(), CFG).reason


def test_position_and_sector_caps_reduce_quantity():
    d = evaluate(prop(stop=99.0), ctx(atr=0.5), CFG)  # risk ~1.05 -> 476 shares, cap 20% -> 199
    cap = next(c for c in d.checks if c["check"] == "position_cap")
    assert cap["outcome"] == "capped" and d.qty == 199
    held = [OpenExposure(1, "A", "IT", 25_000, 400)]
    s = evaluate(prop(stop=99.0), ctx(atr=0.5, exposures=held), CFG)
    assert s.qty == 49  # sector room 30k - 25k = 5k / 100.05
    full = [OpenExposure(1, "A", "IT", 30_000, 400)]
    assert "no room" in evaluate(prop(), ctx(exposures=full), CFG).reason


def test_position_count_duplicates_and_aggregate_risk():
    five = [OpenExposure(i, f"S{i}", f"Sec{i}", 10_000, 500) for i in range(1, 6)]
    assert "max 5" in evaluate(prop(), ctx(exposures=five), CFG).reason
    dup = [OpenExposure(9, "NEW", "Other", 10_000, 100, pending=True)]
    assert "already holding" in evaluate(prop(), ctx(exposures=dup), CFG).reason
    four = [OpenExposure(i, f"S{i}", f"Sec{i}", 10_000, 550) for i in range(1, 5)]  # 2,200 of 2,500 used
    d = evaluate(prop(), ctx(exposures=four), CFG)
    assert d.qty == 59 and next(c for c in d.checks if c["check"] == "aggregate_risk")["outcome"] == "capped"  # 300 / 5.05


def test_cash_reserve_correlation_and_gap_risk():
    d = evaluate(prop(), ctx(cash=25_000), CFG)  # 5k usable above the 20k reserve
    assert d.qty == 49 and next(c for c in d.checks if c["check"] == "cash_reserve")["outcome"] == "capped"
    held = [OpenExposure(1, "TWIN", "Bank", 10_000, 400)]
    assert "correlation 0.91" in evaluate(prop(), ctx(exposures=held, correlations={1: 0.91}), CFG).reason
    g = evaluate(prop(), ctx(adverse_gap_pct=8.0), CFG)  # p95 gap 8% > 5% stop
    assert g.qty == 62 and next(c for c in g.checks if c["check"] == "gap_risk")["outcome"] == "capped"  # 500 / 8.004


# ------------------------------------------------------------------ exits

def test_stop_fills_at_stop_or_gap_open_and_beats_target_on_same_bar():
    p = pos(stop=90)
    assert intraday(p, 95, 99, 89, ExitConfig())[0].reference == 90
    gap = intraday(p, 85, 88, 84, ExitConfig())[0]
    assert gap.reference == 85 and "gap" in gap.reason
    both = intraday(p, 95, 130, 89, ExitConfig())  # touched stop and target: stop assumed first
    assert [e.kind for e in both] == ["stop"]


def test_partial_target_at_2r_and_breakeven_then_trailing():
    p = pos(stop=90)  # risk 10/share -> target 120
    ex = intraday(p, 115, 121, 114, ExitConfig())
    assert ex[0].kind == "partial_target" and ex[0].qty == 33 and ex[0].reference == 120
    p.partial_done = True
    r = after_close(p, close=118, atr=3.0, sessions_held=5, cfg=ExitConfig())
    assert r["new_stop"] == pytest.approx(109.0)  # trailing 118 - 9 beats breakeven 100
    p.stop = r["new_stop"]
    assert after_close(p, close=112, atr=3.0, sessions_held=6, cfg=ExitConfig())["new_stop"] is None  # never lowered


def test_no_trailing_before_threshold_and_time_stop():
    p = pos(stop=90)
    r = after_close(p, close=110, atr=3.0, sessions_held=10, cfg=ExitConfig())  # +1R < 1.5R
    assert r["new_stop"] is None and not r["time_exit"]
    assert after_close(p, close=101, atr=3.0, sessions_held=40, cfg=ExitConfig())["time_exit"]
