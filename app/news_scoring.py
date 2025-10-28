from __future__ import annotations
import re, time, json, os, html
from urllib.parse import quote_plus
from typing import Dict, List, Tuple
import xml.etree.ElementTree as ET
import urllib.request

CACHE_PATH = os.environ.get("SENTINEL_NEWS_CACHE", "/tmp/sentinel-v8-news.json")
CACHE_TTL_SEC = 600          # 10 分鐘
WINDOW_SEC    = 24 * 3600    # 24 小時

BULLY = [
    r"surge", r"rally", r"spike", r"breakout", r"record high", r"bull", r"buy", r"rebound",
    r"增持", r"上漲", r"突破", r"利多", r"看多", r"飆升", r"新高", r"大漲", r"反彈",
]
BEARY = [
    r"plunge", r"drop", r"dump", r"sell\-off", r"bear", r"sell", r"liquidation", r"crash",
    r"拋售", r"下跌", r"跳水", r"利空", r"看空", r"暴跌", r"清算", r"崩", r"下挫",
]

KEYWORDS = {
    "BTC": ["bitcoin", "btc", "比特幣"],
    "ETH": ["ethereum", "eth", "以太幣", "以太坊"],
    "SOL": ["solana", "sol", "索拉納"],
    "BNB": ["bnb", "binance coin", "幣安幣"],
    "XRP": ["xrp", "瑞波"],
    "ADA": ["cardano", "ada", "艾達幣"],
    "DOGE": ["dogecoin", "doge", "狗狗幣"],
    "AVAX": ["avalanche", "avax"],
    "TRX": ["tron", "trx"],
    "LINK": ["chainlink", "link"],
    "MATIC": ["polygon", "matic"],
    "TON": ["ton", "telegram open network", "toncoin"],
    "BCH": ["bitcoin cash", "bch", "比特現金"],
    "LTC": ["litecoin", "ltc", "萊特幣"],
}

# ---------- 基礎工具 ---------- #
def _now() -> int:
    return int(time.time())

def _load_cache() -> Dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def _save_cache(data: Dict) -> None:
    try:
        with open(CACHE_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass

def _google_news_rss(q: str, hl="en-US", gl="US", ceid="US:en") -> str:
    base = "https://news.google.com/rss/search?q="
    return f"{base}{quote_plus(q)}&hl={hl}&gl={gl}&ceid={ceid}"

def _fetch_url(url: str, timeout: int = 10) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()

def _parse_rss(xml_bytes: bytes) -> List[Tuple[str, str, int]]:
    out = []
    try:
        root = ET.fromstring(xml_bytes)
        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link  = (item.findtext("link") or "").strip()
            pub   = (item.findtext("{http://purl.org/dc/elements/1.1/}date") or item.findtext("pubDate") or "")
            pub_ts = _parse_pubdate(pub)
            if title and link:
                out.append((html.unescape(title), link, pub_ts))
    except Exception:
        pass
    return out

def _parse_pubdate(s: str) -> int:
    try:
        import email.utils as eut
        tt = eut.parsedate_to_datetime(s)
        return int(tt.timestamp())
    except Exception:
        return _now()

def _translate_to_zh(text: str) -> str:
    try:
        q = quote_plus(text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-TW&dt=t&q={q}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
        return "".join([seg[0] for seg in data[0] if seg and seg[0]])
    except Exception:
        return text

def _score_text(title: str) -> float:
    t = title.lower()
    score = 0.0
    for p in BULLY:
        if re.search(p, t, re.I): score += 1.0
    for p in BEARY:
        if re.search(p, t, re.I): score -= 1.0
    return score

def _time_weight(pub_ts: int, now_ts: int) -> float:
    dt = now_ts - pub_ts
    if dt < 0: dt = 0
    if dt >= WINDOW_SEC: return 0.0
    return max(0.0, 1.0 - dt / WINDOW_SEC)

def _timeago(ts: int, now_ts: int | None = None) -> str:
    now = int(now_ts or _now())
    d = max(0, now - int(ts))
    if d < 60: return f"{d}秒前"
    m = d // 60
    if m < 60: return f"{m}分鐘前"
    h = m // 60
    return f"{h}小時前"

def _search_queries(symbol: str) -> List[str]:
    sym = symbol.upper()
    words = KEYWORDS.get(sym, [sym])
    qs = []
    for w in words:
        qs.append(_google_news_rss(w, hl="en-US", gl="US", ceid="US:en"))
        qs.append(_google_news_rss(w, hl="zh-TW", gl="TW", ceid="TW:zh-Hant"))
    return qs

# ---------- 核心：計分 + 標題彙整（中文） ---------- #
def _score_and_collect(symbol: str, now_ts: int) -> tuple[int, list]:
    """回傳 (0~100 分, items[dict])；items 含 zh_title/link/pub_ts/weight/raw_score"""
    cache = _load_cache()
    ent = cache.get(symbol)
    if ent and (now_ts - int(ent.get("ts", 0)) < CACHE_TTL_SEC):
        return int(ent.get("score", 0)), ent.get("items", [])

    seen = set()
    total = 0.0
    items: List[Dict] = []
    for url in _search_queries(symbol):
        try:
            raw = _fetch_url(url)
            rows = _parse_rss(raw)
        except Exception:
            rows = []
        for title, link, pub_ts in rows:
            key = (title, link)
            if key in seen: continue
            seen.add(key)
            w = _time_weight(pub_ts, now_ts)
            if w <= 0: continue
            zh_title = _translate_to_zh(title)
            s = _score_text(zh_title)
            total += s * w
            items.append({
                "zh_title": zh_title, "link": link, "pub_ts": int(pub_ts),
                "weight": round(w, 3), "raw_score": s
            })

    # 將 raw (-K..K) 映射到 0..100
    K = 10.0
    raw = max(-K, min(K, total))
    norm = int(round((raw + K) / (2*K) * 100))

    # 以權重 * |raw_score| 排序，挑相對重要的中文標題
    items.sort(key=lambda r: (abs(r.get("raw_score", 0)) * r.get("weight", 0)), reverse=True)
    cache[symbol] = {"ts": now_ts, "score": norm, "items": items[:20]}  # 留 20 則供查詢
    _save_cache(cache)
    return norm, items[:20]

def get_news_score(symbol: str) -> int:
    try:
        score, _ = _score_and_collect(symbol.upper(), _now())
        return score
    except Exception:
        return 0

def recent_headlines(symbol: str, k: int = 3) -> List[Dict]:
    """回傳 [{title_zh, link, timeago}] * k"""
    try:
        _, items = _score_and_collect(symbol.upper(), _now())
        out = []
        for it in items[:max(0, k)]:
            out.append({
                "title_zh": it.get("zh_title", ""),
                "link": it.get("link", ""),
                "timeago": _timeago(int(it.get("pub_ts", 0)))
            })
        return out
    except Exception:
        return []

def batch_news_score(symbols: List[str]) -> Dict[str, int]:
    return {s.upper(): get_news_score(s) for s in symbols}

def batch_recent_headlines(symbols: List[str], k: int = 3) -> Dict[str, List[Dict]]:
    return {s.upper(): recent_headlines(s, k=k) for s in symbols}
