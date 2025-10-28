from __future__ import annotations
import re, time
from typing import List
from app.state_store import get_state, save_state
from app import us_stocks, us_news, news_scoring

# —— 門檻（可視需要調整或改成讀環境變數）—— #
THRESH_RISK_ON_HIGH = 60
THRESH_RISK_ON_LOW  = 40
THRESH_NEWS_HOT     = 70  # BTC/ETH 任一達標即觸發

# 政策/監管正負關鍵詞（中文為主，兼顧英字）
POLICY_POS = [
    r"核准", r"通過", r"利多", r"寬鬆", r"批准", r"合法化", r"ETF.*(通過|批准|核准)",
    r"approve", r"approved", r"approval", r"green light", r"ease", r"easing",
]
POLICY_NEG = [
    r"駁回", r"否決", r"延後", r"延遲", r"制裁", r"禁令", r"起訴", r"訴訟", r"罰款", r"罰金",
    r"reject", r"rejected", r"delay", r"ban", r"sue", r"lawsuit", r"sanction", r"fine",
]

# 僅做主題名稱過濾，鎖定較可能屬政策向的 topic
POLICY_TOPICS_HINT = [
    "FOMC", "Federal Reserve", "CPI", "PCE", "Nonfarm Payrolls",
    "SEC", "ETF", "regulation", "監管", "稅", "通膨", "通脹"
]

def _risk_badge() -> List[str]:
    # 從 us_stocks 組好的區塊中提取 Risk-On 數值
    blk = us_stocks.format_us_block(phase="night")  # 內含 Risk-On：xx
    m = re.search(r"Risk\-On[:：]\s*(\d+)", blk)
    if not m:
        return []
    val = int(m.group(1))
    if val >= THRESH_RISK_ON_HIGH:
        return ["風險開"]
    if val <= THRESH_RISK_ON_LOW:
        return ["風險收"]
    return []

def _policy_badge() -> List[str]:
    # 抓美股中文新聞重點，僅選政策相關主題，做簡易淨分（正負關鍵詞）
    topics = us_news.us_recent_news(k_each=3)  # {topic: [{title_zh, ...}, ...]}
    pos = neg = 0
    for topic, heads in topics.items():
        # 只計算較像政策面的主題
        is_policy_topic = any(hint.lower() in topic.lower() for hint in POLICY_TOPICS_HINT)
        if not is_policy_topic:
            continue
        for h in heads:
            t = h.get("title_zh", "")
            if any(re.search(p, t, re.I) for p in POLICY_POS):
                pos += 1
            if any(re.search(n, t, re.I) for n in POLICY_NEG):
                neg += 1
    if pos == 0 and neg == 0:
        return []
    if pos > neg:
        return ["政策↑"]
    if neg > pos:
        return ["政策↓"]
    return []  # 打平不顯示，避免噪音

def _news_hot_badge() -> List[str]:
    # 以 BTC/ETH 作為整體加密新聞熱度代理
    try:
        s_btc = news_scoring.get_news_score("BTC")
        s_eth = news_scoring.get_news_score("ETH")
        if max(s_btc, s_eth) >= THRESH_NEWS_HOT:
            return ["新聞🔥"]
    except Exception:
        pass
    return []

def compute_badges(max_badges: int = 3) -> List[str]:
    badges: List[str] = []
    badges += _risk_badge()
    if len(badges) < max_badges:
        badges += _policy_badge()
    if len(badges) < max_badges:
        badges += _news_hot_badge()
    # 去重、裁切
    seen = set()
    out: List[str] = []
    for b in badges:
        if b not in seen:
            seen.add(b)
            out.append(b)
        if len(out) >= max_badges:
            break
    return out

def refresh_badges() -> List[str]:
    badges = compute_badges(max_badges=3)
    st = get_state()
    st["badges"] = badges
    st["badges_ts"] = int(time.time())
    save_state(st)
    return badges

def get_badges() -> List[str]:
    st = get_state()
    return list(st.get("badges", []))
