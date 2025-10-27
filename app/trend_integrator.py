# app/trend_integrator.py
import os
from typing import List, Dict
from app.trend import classify, Bar
from app.market import get_recent_bars  # 你現有的取K線/成交量方法

ENABLE_TREND_MODEL = os.getenv("ENABLE_TREND_MODEL", "false").lower() == "true"

def annotate_with_trend(results: List[Dict]) -> List[Dict]:
    """
    傳入你的 raw_strength（或排行榜）list[dict]，
    每個 item 需至少有:
      - id: CoinGecko ID（例如 "bitcoin"）
      - 其他欄位不限制
    這函式會就地加上 trend_phase/icon/note/reasons（若可）。
    """
    if not ENABLE_TREND_MODEL:
        return results

    for item in results:
        cg_id = item.get("id") or item.get("symbol") or ""
        if not cg_id:
            continue
        try:
            bars = []
            # 取近 6 小時、每 15 分的資料（調整看你現有 interval）
            for b in get_recent_bars(cg_id, hours=6, interval_minutes=15):
                bars.append(Bar(
                    ts=b["ts"],
                    price=b["price"],
                    volume=b["volume"],
                    strength=b.get("strength")  # 有就用，沒有 trend 內會用價格動能代理
                ))
            tr = classify(cg_id, bars)
            item["trend_phase"] = tr.phase    # FIRE/BOLT/MOON/IDLE
            item["trend_icon"]  = tr.icon     # 🔥⚡🌙💤
            item["trend_note"]  = tr.note
            item["trend_reasons"] = tr.reasons
        except Exception as e:
            # 保守失敗不影響主流程
            item["trend_phase"] = "IDLE"
            item["trend_icon"]  = "💤"
            item["trend_note"]  = "觀望中性：資料不足或暫無方向"
            item["trend_error"] = str(e)

    return results
