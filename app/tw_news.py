# app/tw_news.py 〔v8R7-TWNEWS〕
# 台股新聞（中文）：抓取 Google News RSS（近 24 小時），無金鑰
from __future__ import annotations
import requests, time, html
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime

RSS_URL = "https://news.google.com/rss/search"

# 關鍵字可自行擴充
TW_NEWS_QUERY = "台股 OR 加權指數 OR 櫃買 OR 大盤 OR 台積電 OR 金管會"

def _fetch_rss(q: str, when: str = "24h", hl: str = "zh-TW", gl: str = "TW") -> str:
    params = {
        "q": f"{q} when:{when}",
        "hl": hl,
        "gl": gl,
        "ceid": "TW:zh-Hant",
    }
    r = requests.get(RSS_URL, params=params, timeout=10)
    r.raise_for_status()
    return r.text

def _timeago(dt: datetime) -> str:
    now = datetime.now(timezone.utc)
    sec = int((now - dt).total_seconds())
    if sec < 60: return f"{sec} 秒前"
    m = sec // 60
    if m < 60: return f"{m} 分鐘前"
    h = m // 60
    if h < 48: return f"{h} 小時前"
    d = h // 24
    return f"{d} 天前"

def _parse_items(xml_text: str, limit: int = 10):
    root = ET.fromstring(xml_text)
    ch = root.find("channel")
    if ch is None: return []
    out = []
    for it in ch.findall("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        pub = it.findtext("{http://www.w3.org/2005/Atom}updated") or it.findtext("pubDate") or ""
        try:
            dt = parsedate_to_datetime(pub) if pub else None
            if dt and not dt.tzinfo:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
        out.append({
            "title": html.unescape(title),
            "link": link,
            "dt": dt,
            "timeago": _timeago(dt) if dt else "",
        })
    # 去重、取前 N
    seen = set(); uniq = []
    for x in out:
        k = x["title"]
        if k and k not in seen:
            seen.add(k); uniq.append(x)
    return uniq[:limit]

def recent_tw_news(k: int = 6) -> list[dict]:
    try:
        xml_text = _fetch_rss(TW_NEWS_QUERY, when="24h")
        return _parse_items(xml_text, limit=k)
    except Exception:
        return []

def format_tw_news_block(k: int = 3) -> str:
    items = recent_tw_news(k=k)
    if not items:
        return "🗞️ 台股新聞（近 24h）：暫無可用項目或取得失敗。"
    lines = ["🗞️ 台股新聞（近 24h）"]
    for i, it in enumerate(items, 1):
        lines.append(f"{i}. {it['title']} 〔{it['timeago']}〕")
    return "\n".join(lines)
