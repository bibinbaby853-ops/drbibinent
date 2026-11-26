"""Backtesting (Phase 7): an in-memory, event-driven replica of the paper daily cycle.

* ``data``        everything a run needs, loaded once (point-in-time universe, raw and adjusted prices, ATR,
                  regimes, scores, corporate actions, benchmark) plus a signal factory for strategy variants
* ``engine``      the daily cycle (same pure risk / ledger / exit / cost functions as the paper portfolio)
* ``metrics``     performance statistics vs the Nifty 50
* ``walkforward`` rolling in-sample selection, out-of-sample evaluation, sensitivity grids
"""
