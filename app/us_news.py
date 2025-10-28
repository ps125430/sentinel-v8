from __future__ import annotations
from typing import List, Dict
from app import news_scoring

# 你可增減
US_SYMBOLS_NEWS = [
    "S&P 500", "Dow Jones", "Nasdaq 100", "FOMC", "Federal Reserve", "CPI", "PCE", "Nonfarm Payrolls",
    "AAPL", "NVDA", "MSFT", "AMZN", "TSLA", "META", "GOOGL", "AMD", "NFLX", "JPM"
]

def us_recent_news(k_each: int = 2) -> Dict[str, List[Dict]]:
    # 對上述關鍵字逐一收集中文新聞（利用 news_scoring 的翻譯/加權/快取）
    out: Dict[str, List[Dict]] = {}
    for kw in US_SYMBOLS_NEWS:
        heads = news_scoring.recent_headlines(kw, k=k_each)
        if heads:
            out[kw] = heads
    return out

def format_us_news_block(k_each: int = 2, max_topics: int = 6) -> str:
    m = us_recent_news(k_each=k_each)
    if not m:
        return "🗞️ 美股新聞：暫無重點或取得失敗。"
    lines = ["🗞️ 美股新聞重點（中文）"]
    # 只取前幾個主題，避免訊息太長
    count = 0
    for topic, heads in m.items():
        lines.append(f"• {topic}")
        for h in heads[:k_each]:
            lines.append(f"  - {h['title_zh']} 〔{h['timeago']}〕")
        count += 1
        if count >= max_topics:
            break
    return "\n".join(lines)
