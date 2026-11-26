"""Deterministic NSE-format fixture files (same layouts as the real archives, invented tickers)."""

from __future__ import annotations

import io
import zipfile
from datetime import date

UDIFF_HEADER = ("TradDt,BizDt,Sgmt,Src,FinInstrmTp,FinInstrmId,ISIN,TckrSymb,SctySrs,XpryDt,FininstrmActlXpryDt,StrkPric,"
                "OptnTp,FinInstrmNm,OpnPric,HghPric,LwPric,ClsPric,LastPric,PrvsClsgPric,UndrlygPric,SttlmPric,OpnIntrst,"
                "ChngInOpnIntrst,TtlTradgVol,TtlTrfVal,TtlNbOfTxsExctd,SsnId,NewBrdLotQty,Rmks,Rsvd1,Rsvd2,Rsvd3,Rsvd4")
FULL_HEADER = ("SYMBOL, SERIES, DATE1, PREV_CLOSE, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, LAST_PRICE, CLOSE_PRICE, AVG_PRICE, "
               "TTL_TRD_QNTY, TURNOVER_LACS, NO_OF_TRADES, DELIV_QTY, DELIV_PER")
LEGACY_HEADER = "SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN,"


def bar(symbol, isin, o, h, l, c, pc, vol, series="EQ"):
    return dict(symbol=symbol, isin=isin, series=series, open=o, high=h, low=l, close=c, prev_close=pc, volume=vol)


def udiff_csv(d: date, rows: list[dict], extra_lines: list[str] = ()) -> bytes:
    lines = [UDIFF_HEADER]
    for i, r in enumerate(rows):
        lines.append(f"{d},{d},CM,NSE,STK,{1000 + i},{r['isin']},{r['symbol']},{r['series']},,,,,{r['symbol']} LTD,"
                     f"{r['open']},{r['high']},{r['low']},{r['close']},{r['close']},{r['prev_close']},,{r['close']},,,"
                     f"{r['volume']},{r['volume'] * r['close']:.2f},{r['volume'] // 10},F1,1,,,,,")
    lines += list(extra_lines)
    return ("\n".join(lines) + "\n").encode()


def full_csv(d: date, rows: list[dict], deliv_pct: float = 50.0) -> bytes:
    ds = d.strftime("%d-%b-%Y")
    lines = [FULL_HEADER]
    for r in rows:
        lines.append(f"{r['symbol']}, {r['series']}, {ds}, {r['prev_close']}, {r['open']}, {r['high']}, {r['low']}, {r['close']}, "
                     f"{r['close']}, {r['close']}, {r['volume']}, {r['volume'] * r['close'] / 1e5:.2f}, {r['volume'] // 10}, "
                     f"{int(r['volume'] * deliv_pct / 100)}, {deliv_pct:.2f}")
    return ("\n".join(lines) + "\n").encode()


def legacy_csv(d: date, rows: list[dict]) -> bytes:
    ds = d.strftime("%d-%b-%Y").upper()
    lines = [LEGACY_HEADER]
    for r in rows:
        lines.append(f"{r['symbol']},{r['series']},{r['open']},{r['high']},{r['low']},{r['close']},{r['close']},{r['prev_close']},"
                     f"{r['volume']},{r['volume'] * r['close']:.2f},{ds},{r['volume'] // 10},{r['isin']},")
    return ("\n".join(lines) + "\n").encode()


def zipped(name: str, payload: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(name, payload)
    return buf.getvalue()


class FakeHttp:
    """Serves a dict of url -> bytes; anything else is a 404 (DataNotPublished)."""

    def __init__(self, files: dict[str, bytes]):
        self.files = files
        self.calls: list[str] = []

    def get(self, url: str) -> bytes:
        from app.market_data.base import DataNotPublished
        self.calls.append(url)
        if url not in self.files:
            raise DataNotPublished(f"404 {url}")
        return self.files[url]
