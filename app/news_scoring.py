from __future__ import annotations
from typing import Dict

# 雛型：先固定 0，之後接 RSS/News API 時替換
def get_news_score(symbol: str) -> int:
    # TODO: 接 Google News / RSS / X，做情緒與熱度加權
    return 0

def batch_news_score(symbols: list[str]) -> Dict[str, int]:
    return {s.upper(): get_news_score(s) for s in symbols}
