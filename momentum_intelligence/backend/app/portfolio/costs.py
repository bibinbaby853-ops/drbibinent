"""Indian equity delivery charges and fill slippage (pure functions)."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from ..config import CostConfig, SlippageConfig


@dataclass(frozen=True)
class Charges:
    brokerage: float
    stt: float
    exchange_txn: float
    sebi_fee: float
    stamp_duty: float
    gst: float
    dp_charge: float
    model: str

    @property
    def total(self) -> float:
        return round(self.brokerage + self.stt + self.exchange_txn + self.sebi_fee + self.stamp_duty + self.gst + self.dp_charge, 2)

    def to_dict(self) -> dict:
        return {**asdict(self), "total": self.total}


def charges(side: str, qty: int, price: float, cfg: CostConfig) -> Charges:
    """Charges for one delivery order. ``side`` is "buy" or "sell"."""
    if side not in ("buy", "sell"):
        raise ValueError(side)
    value = qty * price
    pct = lambda p: value * p / 100  # noqa: E731
    brokerage = min(pct(cfg.brokerage_pct), cfg.brokerage_cap_inr) if cfg.brokerage_pct > 0 else 0.0
    exchange = pct(cfg.exchange_txn_pct)
    sebi = pct(cfg.sebi_fee_pct)
    gst = (brokerage + exchange + sebi) * cfg.gst_pct / 100
    return Charges(brokerage=round(brokerage, 2), stt=round(pct(cfg.stt_pct), 2), exchange_txn=round(exchange, 2),
                   sebi_fee=round(sebi, 2), stamp_duty=round(pct(cfg.stamp_duty_buy_pct), 2) if side == "buy" else 0.0,
                   gst=round(gst, 2), dp_charge=cfg.dp_charge_inr if side == "sell" else 0.0, model=cfg.version)


def slippage_bps(median_value_cr: float | None, cfg: SlippageConfig) -> float:
    if median_value_cr is None:
        return cfg.low_bps
    if median_value_cr >= cfg.high_liquidity_cr:
        return cfg.high_bps
    if median_value_cr >= cfg.medium_liquidity_cr:
        return cfg.medium_bps
    return cfg.low_bps


def fill_price(side: str, reference: float, bps: float) -> float:
    """Buy fills above the reference price, sell fills below it, rounded to the 0.05 tick."""
    raw = reference * (1 + bps / 10_000) if side == "buy" else reference * (1 - bps / 10_000)
    return round(round(raw / 0.05) * 0.05, 2)
