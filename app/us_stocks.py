from __future__ import annotations
import csv, io, time
from typing import Dict, List, Tuple
import urllib.request

# 免金鑰資料源：Stooq CSV
# 範例：https://stooq.com/q/l/?s=spy.us,qqq.us&f=sd2t2ohlcv&h&e=csv
STQ_FMT = "https://stooq.com/q/l/?s={tickers}&f=sd2t2ohlcv&h&e=csv"

# 大盤與風險代理
CORE = ["spy.us","qqq.us","dia.us","iwm.us","vixy.us","uup.us","tlt.us"]
# 板塊 ETF
SECTORS = ["xlk.us","xlf.us","xle.us","xlv.us","xly.us"]

NAME_MAP = {
    "spy.us":"SPY","qqq.us":"QQQ","dia.us":"DIA","iwm.us":"IWM",
    "vixy.us":"VIXY","uup.us":"UUP","tlt.us":"TLT",
    "xlk.us":"XLK","xlf.us":"XLF","xle.us":"XLE","xlv.us":"XLV","xly.us":"XLY",
}

def _fetch_stooq(tickers: List[str]) -> Dict[str, Dict]:
    url = STQ_FMT.format(tickers=",".join(tickers))
    req = urllib.request.Request(url, headers={"User-Agent":"Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read().decode("utf-8", errors="ignore")
    rdr = csv.DictReader(io.StringIO(data))
    out: Dict[str, Dict] = {}
    for row in rdr:
        s = (row.get("Symbol") or "").lower()
        try:
            close = float(row.get("Close") or 0)
            openp = float(row.get("Open") or 0)
        except Exception:
            close, openp = 0.0, 0.0
        chg = 0.0
        if openp > 0:
            chg = (close - openp) / openp * 100.0
        out[s] = {"symbol": NAME_MAP.get(s, s.upper()), "price": close, "chg": chg}
    return out

def get_us_snapshot() -> Dict[str, Dict]:
    quotes = _fetch_stooq(CORE + SECTORS)
    return quotes

def _risk_on_score(q: Dict[str, Dict]) -> int:
    # 粗略：SPY/QQQ/IWM 上漲加分，VIXY 下跌加分，UUP(美元)下跌加分，TLT(長債)上漲加分
    score = 50
    def g(sym): return float(q.get(sym,{}).get("chg",0.0))
    score += 0.8 * g("spy.us") + 0.8 * g("qqq.us") + 0.6 * g("iwm.us")
    score += -0.7 * g("vixy.us")
    score += -0.2 * g("uup.us")
    score += 0.2 * g("tlt.us")
    # 夾在 0~100
    return max(0, min(100, int(round(score))))

def format_us_block(phase: str = "night") -> str:
    """
    phase: 'night' = 台北 22:30（美股開盤雷達）
           'morning' = 台北早上（隔夜回顧）
    """
    q = get_us_snapshot()
    if not q:
        return "美股觀測：暫時無法取得行情資料。"

    risk = _risk_on_score(q)

    core = [q.get("spy.us"), q.get("qqq.us"), q.get("dia.us"), q.get("iwm.us")]
    core_lines = []
    for it in core:
        if not it: continue
        core_lines.append(f"{it['symbol']} {it['chg']:+.2f}%")

    sectors = [q.get(x) for x in ["xlk.us","xlf.us","xle.us","xlv.us","xly.us"]]
    sec_lines = []
    for it in sectors:
        if not it: continue
        sec_lines.append(f"{it['symbol']} {it['chg']:+.2f}%")

    # 美元、波動、債券
    extra = []
    for key in ["uup.us","vixy.us","tlt.us"]:
        it = q.get(key)
        if it:
            extra.append(f"{it['symbol']} {it['chg']:+.2f}%")

    title = "📈 美股開盤雷達" if phase == "night" else "🌙 隔夜美股回顧"
    block = [
        f"{title}｜Risk-On 指數：{risk}",
        "大盤：" + "｜".join(core_lines) if core_lines else "",
        "板塊：" + "｜".join(sec_lines) if sec_lines else "",
        "外圍：" + "｜".join(extra) if extra else "",
    ]
    return "\n".join([x for x in block if x])
