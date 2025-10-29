# app/tw_stocks.py 〔v8R6 新增檔〕
# 台股雷達：Yahoo Quote API（免金鑰）→ 三行分組 & 詳細清單
from __future__ import annotations
import requests, math

# 追蹤清單（台股前十大權值股 + 加權指數）
# 指數符號 Yahoo 用 ^TWII（URL 需轉碼為 %5ETWII）
TW_SYMBOLS = [
    "%5ETWII",  # 加權指數
    "2330.TW","2317.TW","2454.TW","2308.TW","6505.TW",
    "1303.TW","2412.TW","2881.TW","2882.TW","2303.TW"
]

DISPLAY = {
    "%5ETWII": "加權",
    "2330.TW": "台積電","2317.TW": "鴻海","2454.TW": "聯發科",
    "2308.TW": "台達電","6505.TW": "台塑化","1303.TW": "南亞",
    "2412.TW": "中華電","2881.TW": "富邦金","2882.TW": "國泰金",
    "2303.TW": "聯電",
}

def _yahoo_quote(symbols: list[str]) -> list[dict]:
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    q = ",".join(symbols)
    r = requests.get(url, params={"symbols": q}, timeout=10)
    r.raise_for_status()
    data = r.json().get("quoteResponse", {}).get("result", [])
    out = []
    for d in data:
        sym = d.get("symbol", "")
        name = DISPLAY.get(sym, d.get("shortName") or sym)
        price = d.get("regularMarketPrice")
        chg = d.get("regularMarketChange")
        pct = d.get("regularMarketChangePercent")
        out.append({
            "symbol": sym,
            "name": name,
            "price": price,
            "chg": chg,
            "pct": pct,
        })
    return out

def _fmt_delta(pct):
    if pct is None or (isinstance(pct, float) and math.isnan(pct)):
        return "—"
    s = f"{pct:+.1f}%"
    return s

def _group_three_lines(rows: list[dict]) -> str:
    # 跳過指數，三行分組（每行最多 3～4 檔，易讀）
    picks = [r for r in rows if r["symbol"] != "%5ETWII"]
    line1 = picks[0:3]
    line2 = picks[3:6]
    line3 = picks[6:10]
    def line(lst):
        return "｜".join(f'{r["name"]} {_fmt_delta(r["pct"])}' for r in lst if r.get("pct") is not None)
    return "\n".join([s for s in [line(line1), line(line2), line(line3)] if s])

def format_tw_block(phase: str = "intraday") -> str:
    """
    簡版區塊（報表用）：
    第一行顯示加權指數變化，其下三行顯示權值股分組
    """
    rows = _yahoo_quote(TW_SYMBOLS)
    idx = next((r for r in rows if r["symbol"] == "%5ETWII"), None)
    idx_line = f"台股雷達｜{(idx and idx['name']) or '加權'} {_fmt_delta(idx and idx.get('pct'))}"
    tri = _group_three_lines(rows)
    return f"{idx_line}\n{tri}" if tri else idx_line

def format_tw_full() -> str:
    """
    詳細清單（LINE 指令「台股」用）：逐檔一行
    """
    rows = _yahoo_quote(TW_SYMBOLS)
    lines = ["📈 台股觀察清單"]
    for r in rows:
        if r["symbol"] == "%5ETWII":
            lines.append(f"— {r['name']}：{_fmt_delta(r['pct'])}")
        else:
            price = "—" if r["price"] is None else f"{r['price']:.2f}"
            pct = _fmt_delta(r["pct"])
            sign = "🔺" if (r["pct"] or 0) > 0 else ("🔻" if (r["pct"] or 0) < 0 else "⏸️")
            lines.append(f"{sign} {r['name']} {pct}（{price}）")
    return "\n".join(lines)
