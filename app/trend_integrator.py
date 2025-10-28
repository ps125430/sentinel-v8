from __future__ import annotations
import requests, time
from typing import List, Dict, Tuple
from app import news_scoring

COINGECKO = "https://api.coingecko.com/api/v3/coins/markets"
# 常見幣對應（可自行擴充）
SYMBOL_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "BNB": "binancecoin",
    "XRP": "ripple",
    "ADA": "cardano",
    "DOGE": "dogecoin",
    "AVAX": "avalanche-2",
    "TRX": "tron",
    "LINK": "chainlink",
    "MATIC": "matic-network",
    "TON": "the-open-network",
    "BCH": "bitcoin-cash",
    "LTC": "litecoin",
}

def fetch_markets(vs_currency: str = "usd", limit: int = 20) -> List[Dict]:
    ids = ",".join(SYMBOL_MAP.values())
    params = {
        "vs_currency": vs_currency,
        "ids": ids,
        "order": "market_cap_desc",
        "per_page": limit,
        "page": 1,
        "price_change_percentage": "24h",
        "locale": "en",
    }
    r = requests.get(COINGECKO, params=params, timeout=10)
    r.raise_for_status()
    return r.json()

def infer_symbol(coin_id: str) -> str:
    for sym, cid in SYMBOL_MAP.items():
        if cid == coin_id:
            return sym
    return coin_id.upper()

def phase_from_pct(pct24: float) -> str:
    if pct24 >= 5:
        return "🔥"
    if 1 <= pct24 < 5:
        return "⚡"
    if -3 <= pct24 < 1:
        return "💤"
    return "🌙"

def volume_arrow(rel: float) -> str:
    # rel ∈ [0,1]：用粗略分級顯示量能趨勢
    if rel >= 0.67:
        return "量↑"
    if rel >= 0.34:
        return "量→"
    return "量↓"

def rank_normalize(values: List[float]) -> Dict[str, float]:
    # 回傳每個 key 的 0~1 百分位；這個版本會在上游先組 (key,value) 列表
    if not values:
        return {}
    v_sorted = sorted(values)
    idx = {v: i for i, v in enumerate(v_sorted)}
    n = len(values) - 1 if len(values) > 1 else 1
    return {v: idx[v] / n for v in values}

def build_table(scheme: str = "tw") -> Tuple[List[Dict], Dict[str, int]]:
    data = fetch_markets()
    # 量能正規化用
    vols = [float(x.get("total_volume") or 0) for x in data]
    # 我們需要每一列的 volume 百分位
    sorted_vols = sorted(vols)
    def vol_rank(v):
        if len(sorted_vols) <= 1:
            return 0.5
        # 簡單百分位
        pos = 0
        for i, sv in enumerate(sorted_vols):
            if v >= sv:
                pos = i
        return pos / (len(sorted_vols)-1)

    # 批次新聞分數（目前 0）
    syms = [infer_symbol(x["id"]) for x in data]
    news = news_scoring.batch_news_score(syms)

    rows = []
    for x in data:
        sym = infer_symbol(x["id"])
        price = float(x.get("current_price") or 0)
        pct24 = float(x.get("price_change_percentage_24h") or 0)
        vol = float(x.get("total_volume") or 0)
        vr = vol_rank(vol)  # 0~1
        strong = max(0.0, pct24) * vr * 100.0
        news_s = int(news.get(sym, 0))
        total = 0.6 * strong + 0.4 * news_s
        phase = phase_from_pct(pct24)
        rows.append({
            "symbol": sym,
            "price": price,
            "pct24": pct24,
            "volume_rel": vr,
            "phase": phase,
            "score_strong": round(strong, 1),
            "score_news": news_s,
            "score_total": round(total, 1),
        })

    # 依「總分」由高到低
    rows.sort(key=lambda r: r["score_total"], reverse=True)
    return rows, news

def choose_top(rows: List[Dict], topn: int = 3) -> Tuple[List[Dict], List[Dict]]:
    # 多：總分高且 pct24 >= 0
    longs = [r for r in rows if r["pct24"] >= 0]
    shorts = [r for r in rows if r["pct24"] < 0]
    longs = longs[:topn]
    # 空：從尾端挑選跌得多的（用 score_total 但需 pct24<0）
    shorts = sorted(shorts, key=lambda r: r["score_total"])[:topn]  # 分數越低越靠前
    return longs, shorts

def paint_action(scheme: str, action: str) -> str:
    # action: "多" or "空"
    if scheme == "tw":
        return "🟥多" if action == "多" else "🟩空"
    else:
        return "🟩多" if action == "多" else "🟥空"

def arrow(pct: float) -> str:
    if pct >= 5:
        return "↗↗"
    if pct >= 1:
        return "↗"
    if pct <= -5:
        return "↘↘"
    if pct <= -1:
        return "↘"
    return "→"

def format_rows(rows: List[Dict], scheme: str, action: str) -> List[str]:
    out = []
    tag = paint_action(scheme, action)
    for r in rows:
        sym = r["symbol"]
        pct = r["pct24"]
        vol_tag = volume_arrow(r["volume_rel"])
        phase = r["phase"]
        s_str = r["score_strong"]
        s_news = r["score_news"]
        s_total = r["score_total"]
        out.append(f"{tag} {sym} {phase} {arrow(pct)} {pct:+.2f}% ／ {vol_tag} ／ S:{s_str} N:{s_news} T:{s_total}")
    return out

def generate_report(scheme: str = "tw", topn: int = 3) -> str:
    rows, _ = build_table(scheme)
    longs, shorts = choose_top(rows, topn=topn)
    longs_fmt = format_rows(longs, scheme, "多")
    shorts_fmt = format_rows(shorts, scheme, "空")
    msg = []
    msg.append("🚀 今日強勢（做多候選）")
    msg.extend([f"{i+1}. {line}" for i, line in enumerate(longs_fmt)])
    msg.append("")
    msg.append("🧊 今日弱勢（做空候選）")
    msg.extend([f"{i+1}. {line}" for i, line in enumerate(shorts_fmt)])
    return "\n".join(msg)

def generate_side(single: str, scheme: str = "tw", want_strong: bool = True, topn: int = 3) -> str:
    rows, _ = build_table(scheme)
    longs, shorts = choose_top(rows, topn=topn)
    if want_strong:
        lines = format_rows(longs, scheme, "多")
        title = "🚀 今日強勢（做多候選）"
    else:
        lines = format_rows(shorts, scheme, "空")
        title = "🧊 今日弱勢（做空候選）"
    msg = [title]
    msg.extend([f"{i+1}. {line}" for i, line in enumerate(lines)])
    return "\n".join(msg)
