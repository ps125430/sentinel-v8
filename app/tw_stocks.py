# app/tw_stocks.py 〔v8R8-TWLIST〕
# 台股雷達：可動態管理觀察清單；預設追蹤十大權值股＋加權指數
from __future__ import annotations
import requests, math
from app.state_store import get_state, save_state

# 預設清單（含加權指數 %5ETWII）
DEFAULT_TW_SYMBOLS = [
    "%5ETWII",  # 加權指數（Yahoo: ^TWII 的 URL 轉碼）
    "2330.TW","2317.TW","2454.TW","2308.TW","6505.TW",
    "1303.TW","2412.TW","2881.TW","2882.TW","2303.TW"
]

DISPLAY = {
    "%5ETWII": "加權","2330.TW":"台積電","2317.TW":"鴻海","2454.TW":"聯發科",
    "2308.TW":"台達電","6505.TW":"台塑化","1303.TW":"南亞","2412.TW":"中華電",
    "2881.TW":"富邦金","2882.TW":"國泰金","2303.TW":"聯電",
}

def _get_watchlist() -> list[str]:
    st = get_state()
    prefs = st.setdefault("prefs", {})
    wl = prefs.get("tw_watchlist")
    if not wl:
        prefs["tw_watchlist"] = DEFAULT_TW_SYMBOLS.copy()
        save_state()  # 無參數相容
        return DEFAULT_TW_SYMBOLS
    return wl

def _set_watchlist(symbols: list[str]):
    st = get_state()
    st.setdefault("prefs", {})["tw_watchlist"] = symbols
    save_state()  # 無參數相容

def _normalize(sym: str) -> str:
    s = sym.strip().upper()
    if s.startswith("^TWII") or s == "%5ETWII":
        return "%5ETWII"
    if not s.endswith(".TW") and not s.startswith("%5E"):
        s = s + ".TW"
    return s

def add_symbol(sym: str) -> str:
    sym = _normalize(sym)
    wl = _get_watchlist()
    if sym in wl:
        return f"{sym} 已在觀察清單中。"
    wl.append(sym)
    _set_watchlist(wl)
    return f"{sym} 已加入台股觀察清單。"

def remove_symbol(sym: str) -> str:
    sym = _normalize(sym)
    wl = _get_watchlist()
    if sym not in wl:
        return f"{sym} 不在清單中。"
    if sym == "%5ETWII" and wl.count("%5ETWII") == 1:
        return "加權指數不可移除（至少保留一個指數參考）。"
    wl.remove(sym)
    _set_watchlist(wl)
    return f"{sym} 已自觀察清單移除。"

def list_symbols() -> str:
    wl = _get_watchlist()
    names = [DISPLAY.get(s, s) for s in wl]
    return "📊 目前台股觀察清單：\n" + "｜".join(names)

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

def _fmt_delta(pct):
    if pct is None or (isinstance(pct, float) and math.isnan(pct)): return "—"
    return f"{pct:+.1f}%"

def _group_three_lines(rows: list[dict]) -> str:
    picks = [r for r in rows if r["symbol"] != "%5ETWII"]
    if not picks: return ""
    groups = [picks[i:i+3] for i in range(0, len(picks), 3)]
    return "\n".join("｜".join(f'{r["name"]} {_fmt_delta(r["pct"])}' for r in g) for g in groups if g)

def format_tw_block(phase: str = "intraday") -> str:
    syms = _get_watchlist()
    rows = _yahoo_quote(syms)
    idx = next((r for r in rows if r["symbol"] == "%5ETWII"), None)
    head = f"台股雷達｜{(idx and idx['name']) or '加權'} {_fmt_delta(idx and idx.get('pct'))}"
    tri = _group_three_lines(rows)
    return head + ("\n" + tri if tri else "")

def format_tw_full() -> str:
    syms = _get_watchlist()
    rows = _yahoo_quote(syms)
    lines = ["📈 台股觀察清單（即時）"]
    for r in rows:
        sign = "🔺" if (r["pct"] or 0) > 0 else ("🔻" if (r["pct"] or 0) < 0 else "⏸️")
        price = "—" if r["price"] is None else f"{r['price']:.2f}"
        lines.append(f"{sign} {r['name']} {_fmt_delta(r['pct'])}（{price}）")
    return "\n".join(lines)
