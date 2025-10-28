# app/news_scoring.py
from datetime import datetime, timezone

# 最小可用的來源權重（之後再細化）
TRUST = {
    "Bloomberg": 1.0,
    "Reuters": 1.0,
    "Financial Times": 0.9,
    "WSJ": 0.9,
    "CoinDesk": 0.8,
    "The Block": 0.7,
}

# 先放一組開箱即用的關鍵字（可透過環境變數再擴充）
DEFAULT_KEYS = ["bitcoin","btc","ethereum","eth","solana","sol",
                "ETF","SEC","hack","exploit","liquidation","FUD",
                "halving","CPI","rate","ETF inflow","outflow"]

def score_article(a: dict, keys=None) -> int:
    """
    期待欄位：
      a["title"] : str
      a["source"]: str
      a["published_at"]: datetime (UTC)
    """
    if keys is None:
        keys = DEFAULT_KEYS
    title = (a.get("title") or "").lower()
    base = sum(1 for k in keys if k.lower() in title)
    trust = TRUST.get(a.get("source",""), 0.5)

    pub = a.get("published_at")
    if isinstance(pub, datetime):
        age_s = (datetime.now(timezone.utc) - pub).total_seconds()
    else:
        age_s = 999999

    # 24 小時內拉滿，越舊越低
    recency = max(0.0, 1.0 - age_s/86400.0)

    raw = base*22 + trust*30 + recency*50
    return max(0, min(100, int(raw)))

def aggregate_news_score(articles: list) -> int:
    """
    給你一串新聞，回傳 0~100 分（取前 5 高平均）
    """
    if not articles:
        return 0
    scored = sorted((score_article(a) for a in articles), reverse=True)[:5]
    return int(sum(scored)/len(scored))
