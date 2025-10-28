from __future__ import annotations
import re, time, json, os, html
from urllib.parse import quote_plus
from typing import Dict, List, Tuple
import xml.etree.ElementTree as ET
import urllib.request

CACHE_PATH = os.environ.get("SENTINEL_NEWS_CACHE", "/tmp/sentinel-v8-news.json")
CACHE_TTL_SEC = 600
WINDOW_SEC = 24 * 3600

# －－情緒詞典（含中英）－－ #
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

# —— 內部工具 —— #
def _now() -> int:
    return int(time.time())

def _load_cache() -> Dict:
    if os.path.exists(CACHE_PATH):
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
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
            pub   = (item.findtext("{http://purl.org/dc/elements/1.1/}date")
                     or item.findtext("pubDate") or "")
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

# —— 新增：自動翻譯英文為中文 —— #
def _translate_to_zh(text: str) -> str:
    """使用 Google 翻譯輕量版（不需 API key）"""
    try:
        q = quote_plus(text)
        url = f"https://translate.googleapis.com/translate_a/single?client=gtx&sl=auto&tl=zh-TW&dt=t&q={q}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.load(resp)
        # 資料結構：[ [[ "翻譯後句子", "原文", None, None ... ], ...], ...]
        zh = "".join([seg[0] for seg in data[0] if seg and seg[0]])
        return zh
    except Exception:
        return text  # 失敗時原樣返回

def _score_text(title: str) -> float:
    t = title.lower()
    score = 0.0
    for p in BULLY:
        if re.search(p, t, re.I):
            score += 1.0
    for p in BEARY:
        if re.search(p, t, re.I):
            score -= 1.0
    return score

def _time_weight(pub_ts: int, now_ts: int) -> float:
    dt = now_ts - pub_ts
    if dt < 0:
        dt = 0
    if dt >= WINDOW_SEC:
        return 0.0
    return max(0.0, 1.0 - dt / WINDOW_SEC)

def _search_queries(symbol: str) -> List[str]:
    sym = symbol.upper()
    words = KEYWORDS.get(sym, [sym])
    queries = []
    for w in words:
        queries.append(_google_news_rss(w, hl="en-US", gl="US", ceid="US:en"))
        queries.append(_google_news_rss(w, hl="zh-TW", gl="TW", ceid="TW:zh-Hant"))
    return queries

def _score_symbol(symbol: str, now_ts: int) -> int:
    cache = _load_cache()
    ent = cache.get(symbol)
    if ent and (now_ts - int(ent.get("ts", 0)) < CACHE_TTL_SEC):
        return int(ent.get("score", 0))

    seen = set()
    total = 0.0
    cnt = 0
    for url in _search_queries(symbol):
        try:
            raw = _fetch_url(url)
            items = _parse_rss(raw)
        except Exception:
            items = []
        for title, link, pub_ts in items:
            key = (title, link)
            if key in seen:
                continue
            seen.add(key)
            w = _time_weight(pub_ts, now_ts)
            if w <= 0:
                continue
            # —— 先翻譯再判斷 —— #
            zh_title = _translate_to_zh(title)
            s = _score_text(zh_title)
            total += s * w
            cnt += 1

    K = 10.0
    raw = max(-K, min(K, total))
    norm = int(round((raw + K) / (2 * K) * 100))
    cache[symbol] = {"ts": now_ts, "score": norm, "samples": cnt}
    _save_cache(cache)
    return norm

def get_news_score(symbol: str) -> int:
    try:
        return _score_symbol(symbol.upper(), _now())
    except Exception:
        return 0

def batch_news_score(symbols: List[str]) -> Dict[str, int]:
    return {s.upper(): get_news_score(s) for s in symbols}
