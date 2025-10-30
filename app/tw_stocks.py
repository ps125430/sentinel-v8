# app/tw_stocks.py 〔v8R7〕
# 台股雷達：Yahoo Quote API（免金鑰）→ 三行分組 & 詳細清單；支援 show_price

from __future__ import annotations
import requests, math

# 追蹤清單（台股前十大權值股 + 加權指數）
TW_SYMBOLS = [
    "%5ETWII",  # 加權指數（^TWII 的 URL 編碼）
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
        pct = d.get("regularMarketChangePercent")
        out.append({"symbol": sym, "name": name, "price": price, "pct": pct})
    return out

def _fmt_pct(pct):
    if pct is None or (isinstance(pct, float) and math.isnan(pct)):
        return "—"
    return f"{pct:+.1f}%"

def _fmt_price(p):
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "—"
    return f"{p:.2f}".rstrip("0").rstrip(".")

def _group_three_lines(rows: list[dict], show_price: bool) -> str:
    picks = [r for r in rows if r["symbol"] != "%5ETWII"]
    line1 = picks[0:3]; line2 = picks[3:6]; line3 = picks[6:10]
    def cell(r):
        base = f'{r["name"]} {_fmt_pct(r["pct"])}'
        if show_price:
            base = f'{base}（{_fmt_price(r["price"])}）'
        return base
    def join(lst): return "｜".join(cell(r) for r in lst if r.get("pct") is not None)
    return "\n".join([s for s in [join(line1), join(line2), join(line3)] if s])

def format_tw_block(phase: str = "intraday", show_price: bool = True) -> str:
    rows = _yahoo_quote(TW_SYMBOLS)
    idx = next((r for r in rows if r["symbol"] == "%5ETWII"), None)
    idx_line = f'台股雷達｜{(idx and idx["name"]) or "加權"} {_fmt_pct(idx and idx.get("pct"))}'
    tri = _group_three_lines(rows, show_price=show_price)
    return f"{idx_line}\n{tri}" if tri else idx_line

def format_tw_full(show_price: bool = True) -> str:
    rows = _yahoo_quote(TW_SYMBOLS)
    lines = ["📈 台股觀察清單"]
    for r in rows:
        if r["symbol"] == "%5ETWII":
            lines.append(f"— {r['name']}：{_fmt_pct(r['pct'])}")
        else:
            pct = _fmt_pct(r["pct"])
            if show_price:
                lines.append(f"{r['name']} {pct}（{_fmt_price(r['price'])}）")
            else:
                lines.append(f"{r['name']} {pct}")
    return "\n".join(lines)

# （若未來要做「加入台股/移除台股/清單」可在此擴充，目前由 main 處理固定清單）
