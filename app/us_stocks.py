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
        if openp > 0:
            chg = (close - openp) / openp * 100.0
        out[sym] = {"symbol": NAME_MAP.get(sym, sym.upper()), "chg": round(chg, 2)}
    return out

def _risk_on_score(data: Dict[str, Dict]) -> int:
    if not data:
        return 50
    avg = sum([v["chg"] for v in data.values()]) / len(data)
    score = 50 + avg * 10  # ±2% -> ±20 分
    return max(0, min(100, int(round(score))))

def format_us_block(phase: str = "night") -> str:
    """夜報/早報：三行分組（每行 3–4 檔），手機閱讀最順。"""
    q = _fetch_stooq(CORE_TICKERS)
    if not q:
        return "美股觀測：暫時無法取得行情資料。"

    risk = _risk_on_score(q)
    # 依固定順序輸出
    ordered = []
    for t in CORE_TICKERS:
        if t in q:
            v = q[t]
            ordered.append(f"{v['symbol']} {v['chg']:+.2f}%")

    groups = [ordered[0:3], ordered[3:6], ordered[6:10]]
    grouped_lines = "\n".join("｜".join(g) for g in groups if g)

    title = "📈 美股開盤雷達" if phase == "night" else "🌙 隔夜美股回顧"
    return f"{title}｜Risk-On：{risk}\n{grouped_lines}"

def format_us_full() -> str:
    """指令用詳細版：逐檔一行，便於複製與細看。"""
    q = _fetch_stooq(CORE_TICKERS)
    if not q:
        return "美股觀測：暫時無法取得行情資料。"
    risk = _risk_on_score(q)
    lines = [f"📊 美股十巨頭｜Risk-On：{risk}"]
    for t in CORE_TICKERS:
        if t in q:
            v = q[t]
            lines.append(f"{v['symbol']}: {v['chg']:+.2f}%")
    return "\n".join(lines)
