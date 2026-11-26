"""Weekly bars from daily bars (week = Monday-Sunday, labelled by its Monday).

Only completed weeks may feed decisions. With ``as_of`` given, the week that
contains ``as_of`` is dropped as still forming, unless the caller states
``week_closed=True`` (e.g. it is after the close of that week's final session).
Weeks after ``as_of`` are always dropped.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd


def to_weekly(daily: pd.DataFrame, *, as_of: date | None = None, week_closed: bool = False) -> pd.DataFrame:
    """daily: columns session_date, open, high, low, close, volume (sorted or not)."""
    if daily.empty:
        return daily.iloc[0:0]
    d = daily.sort_values("session_date").copy()
    sd = pd.to_datetime(d["session_date"])
    d["week"] = (sd - pd.to_timedelta(sd.dt.day_of_week, unit="D")).dt.date
    agg = d.groupby("week", sort=True).agg(session_date=("session_date", "max"), open=("open", "first"),
                                           high=("high", "max"), low=("low", "min"), close=("close", "last"),
                                           volume=("volume", "sum"), sessions=("session_date", "count"))
    agg = agg.reset_index()
    if as_of is not None:
        agg = agg[agg["week"] <= as_of - timedelta(days=as_of.weekday())]
        current_week = as_of - timedelta(days=as_of.weekday())
        if not week_closed:
            agg = agg[agg["week"] < current_week]
    return agg.reset_index(drop=True)
