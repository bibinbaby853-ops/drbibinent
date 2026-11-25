from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from app.market_data.adjustments import adjust, derive_factors, infer_ratio


def series(closes, prev_closes=None, opens=None, start=date(2026, 11, 2)):
    days = [start + timedelta(days=i) for i in range(len(closes))]
    pcs = prev_closes or [np.nan] + closes[:-1]
    return pd.DataFrame({"session_date": days, "open": opens or closes, "high": closes, "low": closes,
                         "close": closes, "prev_close": pcs, "volume": [1000] * len(closes)})


def test_factor_from_exchange_adjusted_prev_close():
    # 1:2 split on day 3: exchange publishes PREV_CLOSE 50 (adjusted) against an actual prior close of 100
    b = series([98, 100, 51, 52], prev_closes=[np.nan, 98, 50, 51])
    ev = derive_factors(b, symbol="X")
    assert len(ev) == 1 and ev[0].ex_date == b.session_date[2] and ev[0].factor == pytest.approx(0.5)
    assert ev[0].kind == "split_or_bonus" and ev[0].confidence == "high"


def test_tick_rounding_is_not_a_corporate_action():
    b = series([10.00, 10.05], prev_closes=[np.nan, 10.04])
    assert derive_factors(b, symbol="X") == []


def test_ordinary_large_move_is_not_inferred_as_split():
    b = series([100, 80, 96])  # -20% then +20%: within bands, no event
    assert derive_factors(b, symbol="X") == []


def test_ratio_gap_inference_on_real_bnagrochem_split():
    # NSE 16 Nov 2026: prior close 162.66, published PREV_CLOSE 162.66 (unadjusted), open 17.00, close 17.08
    b = series([162.66, 17.08], prev_closes=[154.92, 162.66], opens=[159.0, 17.0])
    ev = derive_factors(b, symbol="BNAGROCHEM")
    assert len(ev) == 1 and ev[0].factor == pytest.approx(0.1) and ev[0].kind == "inferred_split_or_bonus"
    assert ev[0].confidence == "medium" and ev[0].details["ratio"] == "1:10"


@pytest.mark.parametrize("prior,open_,close,expected", [
    (100, 52, 51, 0.5), (100, 34, 33.5, 1 / 3), (100, 21, 20.5, 0.2), (1000, 52, 49, 0.05), (50, 250, 245, 5.0)])
def test_infer_ratio_common_cases(prior, open_, close, expected):
    assert infer_ratio(prior, open_, close)[0] == pytest.approx(expected)


def test_infer_ratio_ignores_moves_inside_bands():
    assert infer_ratio(100, 85, 81) is None


def test_adjust_multiplies_prices_before_ex_date_and_preserves_traded_value():
    b = series([100, 102, 51, 52])
    ex = b.session_date[2]
    a = adjust(b, [(ex, 0.5)])
    assert list(a.close) == [50, 51, 51, 52]
    assert list(a.volume) == [2000, 2000, 1000, 1000]
    assert (a.close * a.volume).iloc[0] == (b.close * b.volume).iloc[0]
    assert list(b.close) == [100, 102, 51, 52]  # raw input untouched


def test_multiple_factors_compound():
    b = series([400, 200, 100, 100])
    a = adjust(b, [(b.session_date[1], 0.5), (b.session_date[2], 0.5)])
    assert list(a.close) == [100, 100, 100, 100]
