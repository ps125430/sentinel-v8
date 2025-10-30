# app/us_stocks.py 〔v8R7〕
# 美股雷達：Stooq/或現行資料源 → 三行分組 & 詳細清單；支援 show_price
from __future__ import annotations
import math
import requests

US_SYMBOLS = ["NVDA","MSFT","AAPL","AMZN","GOOGL","META","TSLA","INTC","AMD","PLTR"]

# 這裡示範用 Yahoo quote（免金鑰）；你原本若有 stooq 可保留原邏輯，回傳結構一致即可
def _yahoo_quote(symbols: list[str]) -> list[dict]:
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    q = ",".join(symbols)
    r = requests.get(url, params={"symbols": q}, timeout=10)
    r.raise_for_status()
    data = r.json().get("quoteResponse", {}).get("result", [])
    out = []
    for d in data:
        sym = d.get("symbol", "")
        price = d.get("regularMarketPrice")
        pct = d.get("regularMarketChangePercent")
        name = d.get("shortName") or sym
        out.append({"symbol": sym, "name": name, "price": price, "pct": pct})
    return out

def _fmt_pct(p):
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "—"
    return f"{p:+.1f}%"

def _fmt_price(p):
    if p is None or (isinstance(p, float) and math.isnan(p)):
        return "—"
    # 美股不加貨幣符號，避免與幅度混淆；由上層加
    return f"{p:.2f}".rstrip("0").rstrip(".")

def _group_three_lines(rows: list[dict], show_price: bool) -> str:
    # 三行分組（3+3+4）
    line1 = rows[0:3]; line2 = rows[3:6]; line3 = rows[6:10]
    def cell(r):
        base = f'{r["symbol"]} {_fmt_pct(r["pct"])}'
        if show_price:
            base = f'{base}（{_fmt_price(r["price"])}）'
        return base
    def join(lst): return "｜".join(cell(r) for r in lst if r.get("pct") is not None)
    return "\n".join([s for s in [join(line1), join(line2), join(line3)] if s])

def format_us_block(phase: str = "night", show_price: bool = True) -> str:
    rows = _yahoo_quote(US_SYMBOLS)
    header = "📈 美股開盤雷達" if phase == "night" else "📈 美股隔夜回顧"
    tri = _group_three_lines(rows, show_price=show_price)
    return f"{header}\n{tri}"

def format_us_full(show_price: bool = True) -> str:
    rows = _yahoo_quote(US_SYMBOLS)
    lines = ["📈 美股觀察清單（十巨頭）"]
    for r in rows:
        if show_price:
            lines.append(f"{r['symbol']} {_fmt_pct(r['pct'])}（{_fmt_price(r['price'])}）")
        else:
            lines.append(f"{r['symbol']} {_fmt_pct(r['pct'])}")
    return "\n".join(lines)
