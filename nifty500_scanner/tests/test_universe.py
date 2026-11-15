from scanner import universe as u


def test_fallback_list_is_large_and_unique():
    rows = u.load_fallback("nifty500")
    syms = [r["symbol"] for r in rows]
    assert len(syms) >= 450
    assert len(syms) == len(set(syms))
    assert "RELIANCE" in syms and "HDFCBANK" in syms


def test_parse_constituent_csv():
    text = "Company Name,Industry,Symbol,Series,ISIN Code\nFoo Ltd,IT,FOO,EQ,X\n,,,,\n"
    rows = u._parse_constituent_csv(text)
    assert rows == [{"symbol": "FOO", "name": "Foo Ltd", "industry": "IT"}]


def test_yahoo_ticker_roundtrip():
    assert u.to_yahoo_ticker("m&m") == "M&M.NS"
    assert u.from_yahoo_ticker("TCS.NS") == "TCS"


def test_offline_universe_uses_snapshot():
    rows = u.get_universe("nifty500", offline=True)
    assert len(rows) >= 450


def test_symbol_file(tmp_path):
    p = tmp_path / "list.txt"
    p.write_text("# mine\nreliance\nTCS\n")
    assert [r["symbol"] for r in u.load_symbol_file(p)] == ["RELIANCE", "TCS"]
