"""In-memory portfolio state and double-checked accounting (pure; persisted by ``service``).

Invariants (``reconcile``):
* cash = initial capital - sum(buy value + buy charges) + sum(sell value - sell charges)
* realised P&L = sum over sells of (sell price - average cost) x qty - sell charges - buy charges of the sold qty
* equity = cash + sum(qty x mark price)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class Position:
    key: str                  # stable id within the portfolio (e.g. db id or symbol:open date)
    stock_id: int
    symbol: str
    sector: str
    qty: int
    avg_price: float          # includes nothing but the fill price; charges are tracked separately
    initial_stop: float
    stop: float
    initial_risk: float       # qty_at_entry x (entry fill - initial stop), rupees
    entry_date: date
    strategy: str
    highest_close: float
    entry_qty: int
    buy_charges: float        # remaining (unallocated) buy charges for the open qty
    partial_done: bool = False
    target: float | None = None
    mark: float | None = None

    @property
    def risk_per_share(self) -> float:
        return self.initial_risk / self.entry_qty if self.entry_qty else 0.0

    @property
    def market_value(self) -> float:
        return self.qty * (self.mark if self.mark is not None else self.avg_price)

    @property
    def open_risk(self) -> float:
        return max(0.0, (self.avg_price - self.stop)) * self.qty


@dataclass
class Ledger:
    initial_capital: float
    cash: float
    positions: dict[str, Position] = field(default_factory=dict)
    realised_pnl: float = 0.0
    total_charges: float = 0.0
    gross_bought: float = 0.0
    gross_sold: float = 0.0
    buy_charges_total: float = 0.0
    sell_charges_total: float = 0.0

    @classmethod
    def new(cls, capital: float) -> "Ledger":
        return cls(initial_capital=capital, cash=capital)

    # ---- valuation
    @property
    def invested(self) -> float:
        return sum(p.market_value for p in self.positions.values())

    @property
    def equity(self) -> float:
        return self.cash + self.invested

    @property
    def unrealised_pnl(self) -> float:
        return sum((p.mark - p.avg_price) * p.qty for p in self.positions.values() if p.mark is not None)

    def sector_exposure(self) -> dict[str, float]:
        out: dict[str, float] = {}
        for p in self.positions.values():
            out[p.sector] = out.get(p.sector, 0.0) + p.market_value
        return out

    def aggregate_initial_risk(self) -> float:
        return sum(p.initial_risk * p.qty / p.entry_qty for p in self.positions.values() if p.entry_qty)

    def mark(self, prices: dict[int, float]) -> None:
        for p in self.positions.values():
            if p.stock_id in prices and prices[p.stock_id] is not None:
                p.mark = prices[p.stock_id]

    # ---- transactions
    def buy(self, pos: Position, fill: float, charges: float) -> None:
        cost = pos.qty * fill
        if cost + charges > self.cash + 1e-6:
            raise ValueError(f"insufficient cash for {pos.symbol}: need {cost + charges:.2f}, have {self.cash:.2f}")
        if pos.key in self.positions:
            raise ValueError(f"position {pos.key} already open")
        self.cash -= cost + charges
        self.gross_bought += cost
        self.buy_charges_total += charges
        self.total_charges += charges
        pos.avg_price, pos.buy_charges, pos.mark = fill, charges, fill
        self.positions[pos.key] = pos

    def sell(self, key: str, qty: int, fill: float, charges: float) -> float:
        """Sell ``qty`` of a position; returns realised P&L of this sale (after its share of charges)."""
        p = self.positions[key]
        if not 0 < qty <= p.qty:
            raise ValueError(f"cannot sell {qty} of {p.qty}")
        proceeds = qty * fill
        allocated_buy = p.buy_charges * qty / p.qty
        pnl = (fill - p.avg_price) * qty - charges - allocated_buy
        self.cash += proceeds - charges
        self.gross_sold += proceeds
        self.sell_charges_total += charges
        self.total_charges += charges
        self.realised_pnl += pnl
        p.buy_charges -= allocated_buy
        p.qty -= qty
        if p.qty == 0:
            del self.positions[key]
        return pnl

    def adjust_for_corporate_action(self, stock_id: int, factor: float) -> list[str]:
        """Split/bonus: prices x factor, quantity / factor (rounded down; NSE pays fractions in cash, ignored here)."""
        touched = []
        for p in self.positions.values():
            if p.stock_id == stock_id:
                new_qty = int(p.qty / factor + 1e-9)
                p.entry_qty = int(p.entry_qty / factor + 1e-9)
                p.qty = new_qty
                for attr in ("avg_price", "initial_stop", "stop", "highest_close"):
                    setattr(p, attr, getattr(p, attr) * factor)
                if p.target is not None:
                    p.target *= factor
                if p.mark is not None:
                    p.mark *= factor
                touched.append(p.key)
        return touched

    def reconcile(self, tolerance: float = 0.01) -> dict:
        expected_cash = self.initial_capital - self.gross_bought - self.buy_charges_total + self.gross_sold - self.sell_charges_total
        cost_basis_open = sum(p.avg_price * p.qty for p in self.positions.values())
        open_buy_charges = sum(p.buy_charges for p in self.positions.values())
        # Everything bought is either still held (at cost) or was sold: realised = sold - cost of sold - all sell charges - allocated buy charges
        expected_realised = (self.gross_sold - (self.gross_bought - cost_basis_open)
                             - self.sell_charges_total - (self.buy_charges_total - open_buy_charges))
        ok_cash = abs(expected_cash - self.cash) <= tolerance
        ok_pnl = abs(expected_realised - self.realised_pnl) <= tolerance
        ok_eq = abs(self.equity - (self.cash + sum(p.market_value for p in self.positions.values()))) <= tolerance
        return {"ok": ok_cash and ok_pnl and ok_eq, "cash": round(self.cash, 2), "expected_cash": round(expected_cash, 2),
                "realised_pnl": round(self.realised_pnl, 2), "expected_realised_pnl": round(expected_realised, 2),
                "equity": round(self.equity, 2), "charges": round(self.total_charges, 2)}
