from __future__ import annotations
import csv, io, urllib.request
from typing import Dict, List

# Stooq 免費行情 API，免金鑰
STQ_FMT = "https://stooq.com/q/l/?s={tickers}&f=sd2t2ohlcv&h&e=csv"

# 聚焦科技十巨頭
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
        if openp > 0:
            chg = (close - openp) / openp * 100.0
        out[sym] = {"symbol": NAME_MAP.get(sym, sym.upper()), "chg": round(chg, 2)}
    return out

def _risk_on_score(data: Dict[str, Dict]) -> int:
    """以科技巨頭平均漲幅計算 Risk-On 分數"""
    if not data:
        return 50
    avg = sum([v["chg"] for v in data.values()]) / len(data)
    # +2% 以上算風險開，-2% 以下算風險收
    score = 50 + avg * 10
    return max(0, min(100, int(round(score))))

def format_us_block(phase: str = "night") -> str:
    """
    美股十巨頭觀測
    phase: 'night' 美股開盤雷達
           'morning' 隔夜回顧
    """
    q = _fetch_stooq(CORE_TICKERS)
    if not q:
        return "美股觀測：暫時無法取得行情資料。"

    risk = _risk_on_score(q)
    lines = []
    for sym in [
        "NVDA", "MSFT", "AAPL", "AMZN", "GOOGL",
        "META", "TSLA", "INTC", "AMD", "PLTR",
    ]:
        for k, v in q.items():
            if v["symbol"] == sym:
                lines.append(f"{v['symbol']} {v['chg']:+.2f}%")
                break

    title = "📈 美股開盤雷達" if phase == "night" else "🌙 隔夜美股回顧"
    block = [
        f"{title}｜Risk-On：{risk}",
        "主要科技股：" + "｜".join(lines)
    ]
    return "\n".join(block)
