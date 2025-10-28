from __future__ import annotations
import csv, io, urllib.request
from typing import Dict, List

STQ_FMT = "https://stooq.com/q/l/?s={tickers}&f=sd2t2ohlcv&h&e=csv"

CORE_TICKERS = [
    "nvda.us", "msft.us", "aapl.us", "amzn.us", "googl.us",
    "meta.us", "tsla.us", "intc.us", "amd.us", "pltr.us"
]

NAME_MAP = {
    "nvda.us": "NVDA",
    "msft.us": "MSFT",
    "aapl.us": "AAPL",
    "amzn.us": "AMZN",
    "googl.us": "GOOGL",
    "meta.us": "META",
    "tsla.us": "TSLA",
    "intc.us": "INTC",
    "amd.us": "AMD",
    "pltr.us": "PLTR",
}

def _fetch_stooq(tickers: List[str]) -> Dict[str, Dict]:
    url = STQ_FMT.format(tickers=",".join(tickers))
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read().decode("utf-8", errors="ignore")
    rdr = csv.DictReader(io.StringIO(data))
    out: Dict[str, Dict] = {}
    for row in rdr:
        sym = (row.get("Symbol") or "").lower()
        try:
            close = float(row.get("Close") or 0)
            openp = float(row.get("Open") or 0)
        except Exception:
            continue
        chg = 0.0
        if o
