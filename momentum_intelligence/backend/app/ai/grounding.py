"""Grounding check: every number in an AI narrative must come from the data it was given.

Numbers are extracted from the text (digits with optional Indian/Western thousands separators,
decimals, sign, % and Rs/₹ are ignored for matching). A number is supported when some value in the
data, rounded to the number of decimals the text shows, equals it (so 18.4669 supports "18.47",
"18.5" and "18"). Allowed besides data values: the parts of dates present in the data (year, month,
day), list lengths, and the session's own date. Anything else is reported as unsupported and the
narrative is rejected; the deterministic report is shown instead.
"""

from __future__ import annotations

import math
import re
from datetime import date

NUM = re.compile(r"(?<![\w.])[-+−]?\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|(?<![\w.])[-+−]?\d+(?:\.\d+)?")


def numbers_in(text: str) -> list[str]:
    # ISO dates are checked as dates, not as three numbers
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)
    return [m.group(0) for m in NUM.finditer(text)]


def _value(tok: str) -> tuple[float, int]:
    t = tok.replace(",", "").replace("−", "-").lstrip("+")
    decimals = len(t.split(".")[1]) if "." in t else 0
    return float(t), decimals


def allowed_values(data) -> tuple[set[float], set[str]]:
    vals: set[float] = set()
    dates: set[str] = set()

    def walk(x):
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            if math.isfinite(x):
                vals.add(float(x))
            return
        if isinstance(x, str):
            for d in re.findall(r"\b(\d{4})-(\d{2})-(\d{2})\b", x):
                dates.add("-".join(d))
                vals.update({float(d[0]), float(int(d[1])), float(int(d[2]))})
            for tok in numbers_in(x):          # numbers inside data strings (e.g. "15:30 IST") count as data
                vals.add(_value(tok)[0])
            return
        if isinstance(x, dict):
            for v in x.values():
                walk(v)
            return
        if isinstance(x, (list, tuple)):
            vals.add(float(len(x)))
            for v in x:
                walk(v)
    walk(data)
    return vals, dates


def supported(tok: str, vals: set[float]) -> bool:
    v, dec = _value(tok)
    for a in vals:
        for cand in (a, -a, abs(a)):
            if round(cand, dec) == round(v, dec) or round(cand, dec) == v:
                return True
    return False


def check(text: str, data) -> dict:
    vals, dates = allowed_values(data)
    bad_dates = [d for d in re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text) if d not in dates]
    toks = numbers_in(text)
    unsupported = sorted({t for t in toks if not supported(t, vals)})
    return {"ok": not unsupported and not bad_dates, "numbers_checked": len(toks), "unsupported": unsupported,
            "unsupported_dates": bad_dates}
