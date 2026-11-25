"""Structural guard: the codebase must not contain broker order placement."""

import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
FORBIDDEN = [
    r"place_order\s*\(", r"modify_order\s*\(", r"cancel_order\s*\(",   # Kite Connect order methods
    r"/orders/regular", r"/orders/amo", r"kiteconnect", r"KiteConnect\(",
]


def test_no_broker_order_calls_in_app_code():
    hits = []
    for path in APP.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for pat in FORBIDDEN:
            if re.search(pat, text):
                hits.append(f"{path.relative_to(APP)}: {pat}")
    assert not hits, "live order placement code found: " + "; ".join(hits)
