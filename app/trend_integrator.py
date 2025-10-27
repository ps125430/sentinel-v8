# app/trend_integrator.py
import os
from typing import List, Dict

TH_LONG  = float(os.getenv("TH_LONG", "70"))
TH_SHORT = float(os.getenv("TH_SHORT", "65"))
ENABLE_TREND_MODEL = os.getenv("ENABLE_TREND_MODEL", "false").lower() == "true"

def _volume_rank_flags(items: List[Dict]):
    vols = [max(0.0, float(x.get("volume", 0))) for x in items]
    if not vols:
        return {}
    s = sorted(vols)
    def pct(v):
        return (sum(1 for z in s if z <= v) / len(s)) if len(s) else 0.5
    return {id(x): pct(max(0.0, float(x.get("volume", 0)))) for x in items}

def annotate_with_trend(raw_strength: List[Dict], th_long: float = TH_LONG, th_short: float = TH_SHORT):
    if not ENABLE_TREND_MODEL:
        return raw_strength

    vol_pct = _volume_rank_flags(raw_strength)

    for it in raw_strength:
        s = float(it.get("score_strong", 0.0))
        chg = float(it.get("chg24h", 0.0))
        vp = vol_pct.get(id(it), 0.5)

        if s >= th_long and chg >= 2.0 and vp >= 0.5:
            it["trend_phase"] = "FIRE"
            it["trend_icon"]  = "🔥"
            it["trend_note"]  = "主升浪：可做多，建議延長監控"
            continue

        if (th_long-5) <= s < th_long and chg >= 0.8:
            it["trend_phase"] = "BOLT"
            it["trend_icon"]  = "⚡"
            it["trend_note"]  = "接棒上攻：密切監控，時機可切入"
            continue
        if s >= th_long and 0.0 <= chg < 2.0 and vp >= 0.4:
            it["trend_phase"] = "BOLT"
            it["trend_icon"]  = "⚡"
            it["trend_note"]  = "接棒上攻：動能成形中"
            continue

        if s >= (th_long-8) and (chg < 0.0 or vp < 0.35):
            it["trend_phase"] = "MOON"
            it["trend_icon"]  = "🌙"
            it["trend_note"]  = "轉弱背離：別追高，考慮停利"
            continue

        it["trend_phase"] = "IDLE"
        it["trend_icon"]  = "💤"
        it["trend_note"]  = "觀望中性：先看戲不出手"

    return raw_strength
