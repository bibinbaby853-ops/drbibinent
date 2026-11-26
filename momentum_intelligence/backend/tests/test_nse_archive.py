from datetime import date

import pytest

from app.market_data import nse_archive as n
from app.market_data.base import DataMismatch, DataNotPublished
from tests.fixtures.nse import FakeHttp, bar, full_csv, legacy_csv, udiff_csv, zipped

D = date(2026, 11, 20)
ROWS = [bar("ALPHA", "INE000A01011", 100, 105, 99, 104, 100, 1000),
        bar("BETA", "INE000B01011", 50, 51, 49, 50.5, 50, 2000, series="BE")]
DEBT = "2026-11-20,2026-11-20,CM,NSE,STK,9,IN0020200104,SGBX,GB,,,,,GOLD BOND,1,1,1,1,1,1,,1,,,1,1,1,F1,1,,,,,"


def test_parse_udiff_filters_to_main_board_equities():
    df = n.parse_udiff(udiff_csv(D, ROWS, [DEBT]), D)
    assert list(df["symbol"]) == ["ALPHA", "BETA"]
    a = df.iloc[0]
    assert (a["isin"], a.open, a.high, a.low, a.close, a.prev_close, a.volume) == ("INE000A01011", 100, 105, 99, 104, 100, 1000)
    assert a.session_date == D and a.ts.hour == 9 and a.ts.minute == 15


def test_parse_full_bhav_converts_lakhs_and_reads_delivery():
    df = n.parse_full_bhav(full_csv(D, ROWS, deliv_pct=40), D)
    a = df[df.symbol == "ALPHA"].iloc[0]
    assert a.turnover_inr == pytest.approx(104_000, rel=1e-3)
    assert a.delivery_qty == 400 and a.delivery_pct == 40


def test_parse_legacy_layout():
    df = n.parse_legacy(legacy_csv(D, ROWS), D)
    assert df.iloc[0]["isin"] == "INE000A01011" and df.iloc[1].series == "BE"


@pytest.mark.parametrize("parser,maker", [(n.parse_udiff, udiff_csv), (n.parse_full_bhav, full_csv), (n.parse_legacy, legacy_csv)])
def test_file_for_another_session_is_rejected(parser, maker):
    with pytest.raises(DataMismatch):
        parser(maker(date(2026, 11, 19), ROWS), D)


def test_merge_delivery_matches_on_symbol_and_series():
    merged = n.merge_delivery(n.parse_udiff(udiff_csv(D, ROWS), D), n.parse_full_bhav(full_csv(D, ROWS[:1]), D))
    assert merged.loc[merged.symbol == "ALPHA", "delivery_pct"].iat[0] == 50
    assert merged.loc[merged.symbol == "BETA", "delivery_pct"].isna().all()


def test_reference_file_parsers():
    eq = n.parse_equity_list(b"SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
                             b"ALPHA,Alpha Ltd,EQ,06-OCT-2008,5,1,INE000A01011,5\n")
    assert eq[0].isin == "INE000A01011" and eq[0].listing_date == date(2008, 10, 6) and eq[0].face_value == 5
    sc = n.parse_symbol_changes(b"NAME OF THE COMPANY,OLD SYMBOL,NEW SYMBOL,EFFECTIVE DATE\nAlpha Ltd,ALPHA,ALPHANEW,19-NOV-2026\nbad,row\n")
    assert sc[0].old_symbol == "ALPHA" and sc[0].effective == date(2026, 11, 19) and len(sc) == 1
    ind = n.parse_industry_list(b"Company Name,Industry,Symbol,Series,ISIN Code\nAlpha Ltd.,Capital Goods,ALPHA,EQ,INE000A01011\n")
    assert ind == {"INE000A01011": "Capital Goods"}


def test_provider_merges_delivery_and_records_lineage(tmp_path):
    http = FakeHttp({n.udiff_url(D): zipped("x.csv", udiff_csv(D, ROWS)), n.full_bhav_url(D): full_csv(D, ROWS)})
    f = n.NSEArchiveProvider(tmp_path, http=http).bhavcopy(D)
    assert len(f.data) == 2 and f.data["delivery_pct"].notna().all()
    assert [l.dataset for l in f.lineage] == ["udiff", "full_bhav"]
    assert all(l.licence == "exchange_official" and len(l.content_sha256) == 64 for l in f.lineage)
    # second read comes from the cache: no new network calls, same hash
    calls = len(http.calls)
    g = n.NSEArchiveProvider(tmp_path, http=http).bhavcopy(D)
    assert len(http.calls) == calls and g.lineage[0].from_cache and g.lineage[0].content_sha256 == f.lineage[0].content_sha256


def test_provider_falls_back_to_legacy_and_notes_missing_delivery(tmp_path):
    old = date(2023, 11, 24)
    http = FakeHttp({n.legacy_url(old): zipped("cm.csv", legacy_csv(old, ROWS))})
    f = n.NSEArchiveProvider(tmp_path, http=http).bhavcopy(old)
    assert f.lineage[0].dataset == "legacy_bhav" and f.data["delivery_pct"].isna().all()
    assert any("delivery figures unavailable" in x for x in f.notes)


def test_holiday_placeholder_full_bhav_is_not_cached(tmp_path):
    # NSE served the previous session's full bhavcopy under the holiday's date (seen 24 Nov 2026).
    http = FakeHttp({n.udiff_url(D): zipped("x.csv", udiff_csv(D, ROWS)), n.full_bhav_url(D): full_csv(date(2026, 11, 19), ROWS)})
    p = n.NSEArchiveProvider(tmp_path, http=http)
    f = p.bhavcopy(D)
    assert f.data["delivery_pct"].isna().all() and any("do not match" in x for x in f.notes)
    assert not (p.cache_dir / "full_bhav" / f"{D:%Y%m%d}.csv").exists()


def test_no_bhavcopy_raises_not_published(tmp_path):
    with pytest.raises(DataNotPublished):
        n.NSEArchiveProvider(tmp_path, http=FakeHttp({})).bhavcopy(D)


def test_offline_mode_never_touches_network(tmp_path):
    http = FakeHttp({})
    with pytest.raises(DataNotPublished):
        n.NSEArchiveProvider(tmp_path, http=http, offline=True).bhavcopy(D)
    assert http.calls == []


def test_intraday_and_index_are_honestly_unavailable(tmp_path):
    from app.market_data.base import ProviderUnavailable
    p = n.NSEArchiveProvider(tmp_path, http=FakeHttp({}))
    with pytest.raises(ProviderUnavailable):
        p.intraday_bars(["ALPHA"], "15m", D, D)
    with pytest.raises(ProviderUnavailable):
        p.index_bars("NIFTY 50", D, D)


def test_rights_entitlements_are_not_equities():
    rows = ROWS + [bar("ALPHA-RE", "INE000A20011", 1, 1.2, 0.9, 1.1, 1, 500, series="BE"),
                   bar("ALPHA-RE1", "INE000A20029", 1, 1.2, 0.9, 1.1, 1, 500, series="BE")]
    assert list(n.parse_udiff(udiff_csv(D, rows), D)["symbol"]) == ["ALPHA", "BETA"]


def test_excel_workbook_served_as_csv_is_read(tmp_path):
    import io as _io
    import pandas as pd
    buf = _io.BytesIO()
    pd.read_csv(_io.BytesIO(full_csv(D, ROWS)), dtype=str).to_excel(buf, index=False)
    http = FakeHttp({n.udiff_url(D): zipped("x.csv", udiff_csv(D, ROWS)), n.full_bhav_url(D): buf.getvalue()})
    f = n.NSEArchiveProvider(tmp_path, http=http).bhavcopy(D)
    assert f.data["delivery_pct"].notna().all() and f.notes == []


def test_unreadable_files_fail_the_session_or_skip_delivery(tmp_path):
    from app.market_data.base import ProviderError
    http = FakeHttp({n.udiff_url(D): zipped("x.csv", udiff_csv(D, ROWS)), n.full_bhav_url(D): b"\x90\x91garbage"})
    f = n.NSEArchiveProvider(tmp_path, http=http).bhavcopy(D)
    assert len(f.data) == 2 and any("unreadable full_bhav" in x for x in f.notes)
    bad = FakeHttp({n.udiff_url(D): b"not a zip"})
    with pytest.raises(ProviderError, match="unreadable udiff"):
        n.NSEArchiveProvider(tmp_path / "b", http=bad).bhavcopy(D)


def test_excel_detection_does_not_depend_on_zip_entry_order():
    import io as _io
    import zipfile as _zf
    import pandas as pd
    buf = _io.BytesIO()
    pd.read_csv(_io.BytesIO(full_csv(D, ROWS)), dtype=str).to_excel(buf, index=False)
    src = _zf.ZipFile(_io.BytesIO(buf.getvalue()))
    out = _io.BytesIO()
    with _zf.ZipFile(out, "w", _zf.ZIP_DEFLATED) as z:   # same workbook, entries written in reverse order
        for name in reversed(src.namelist()):
            z.writestr(name, src.read(name))
    df = n.parse_full_bhav(out.getvalue(), D)
    assert df["delivery_pct"].notna().all()
    assert not n._is_xlsx(zipped("x.csv", udiff_csv(D, ROWS)))   # a zipped CSV is not a workbook
