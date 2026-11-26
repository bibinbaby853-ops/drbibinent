"""Zerodha Kite Connect adapter: NOT AVAILABLE in this environment.

``api.kite.trade`` is blocked by the network policy and no API key or access
token is configured. Every call raises ``ProviderUnavailable`` with the reason;
nothing pretends to work.

By design this adapter is market-data only. It has no order, GTT or
portfolio-mutation methods and must never gain any: the platform is paper
trading only (see docs/DECISIONS.md and tests/test_no_live_trading.py).
"""

from __future__ import annotations

from ..config import get_settings
from .base import ProviderUnavailable

REASON = ("Kite Connect is not available: api.kite.trade is not reachable from this environment and "
          "MI_KITE_API_KEY / access token are not configured. Use the NSE archive and Yahoo providers.")


class KiteProvider:
    name = "kite"
    licence = "licensed"

    def __init__(self):
        s = get_settings()
        self.configured = s.kite_api_key is not None and s.kite_api_secret is not None

    def _unavailable(self, *_, **__):
        raise ProviderUnavailable(REASON)

    daily_bars = intraday_bars = index_bars = security_master = _unavailable
