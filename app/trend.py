# app/trend.py
from dataclasses import dataclass
from typing import List, Dict, Tuple

@dataclass
class Bar:
    ts: int           # 秒 timestamp
    price: float      # 收盤價
    volume: float     # 該區間成交量
    strength: float   # 你原本算出的 0~100 強度分數（若無，傳 None）

@dataclass
class TrendResult:
    phase: str        # 'FIRE' | 'BOLT' | 'MOON' | 'IDLE'
    icon: str         # 🔥 ⚡ 🌙 💤
    note: str         # 短句建議
    reasons: List[str]# 診斷依據（for raw=1）

DEFAULTS = {
    "TH_LONG": 70.0,
    "TH_SHORT": 65.0,
    "MIN_SLOPE_FIRE": 5.0,   # 強度變化的最低斜率門檻（points/小時）
    "MIN_SLOPE_BOLT": 2.0,
    "VOL_BOOST_FIRE": 1.05,  # 量能相對於自身近 24h 平均的放大量
    "VOL_WEAK_MOON": 0.95,
    "LOOKBACK_H": 6,         # 觀察 3~6h 都行；不足以較短回退
}

def _slope(vals: List[Tuple[int, float]]) -> float:
    # 簡單線性回歸斜率（x=時間(小時)、y=值）
    if len(vals) < 2: return 0.0
    xs = [(t - vals[0][0]) / 3600.0 for t, _ in vals]
    ys = [v for _, v in vals]
    n = len(xs)
    sx = sum(xs); sy = sum(ys)
    sxx = sum(x*x for x in xs); sxy = sum(x*y for x,y in zip(xs,ys))
    den = n*sxx - sx*sx
    if den == 0: return 0.0
    return (n*sxy - sx*sy) / den  # 每小時變化量

def _ema_delta(strength_series: List[float], alpha: float=0.6) -> float:
    if not strength_series: return 0.0
    ema = strength_series[0]
    for v in strength_series[1:]:
        ema = alpha*v + (1-alpha)*ema
    return strength_series[-1] - ema

def _vol_ratio(bars: List[Bar]) -> float:
    if not bars: return 1.0
    # 近 1h 平均 vs 近 24h 平均的 proxy：
    # 取最後 1~3 根平均當「近 1h」，全體平均當「近 24h」
    last_n = bars[-3:] if len(bars) >= 3 else bars
    v1 = sum(b.volume for b in last_n) / max(1,len(last_n))
    v24 = sum(b.volume for b in bars) / max(1,len(bars))
    if v24 <= 0: return 1.0
    return v1 / v24

def classify(
    symbol: str,
    bars: List[Bar],
    th_long: float = DEFAULTS["TH_LONG"],
    th_short: float = DEFAULTS["TH_SHORT"],
) -> TrendResult:
    """
    傳入同一幣種按時間遞增的 bars（建議 12~36 根，5~15 分 K 皆可）。
    你原本有 strength 就填進來，沒有就先以價格動能 proxy（下面會處理）。
    """
    reasons: List[str] = []
    if len(bars) < 3:
        return TrendResult("IDLE","💤","資料太少，維持觀望",["bars<3"])

    # 構建 strength_series（若缺則用價變動 proxy）
    strengths = [b.strength for b in bars]
    if any(s is None for s in strengths):
        # 用價格動能 proxy：標準化後映射到 0~100
        prices = [b.price for b in bars]
        p0, pN = prices[0], prices[-1]
        pct = (pN - p0) / p0 * 100 if p0>0 else 0
        # 粗略：中段 50，±10% 映射到 ±25 分
        base = 50 + max(min(pct,10),-10) * 2.5
        strengths = []
        for i,p in enumerate(prices):
            if i==0: strengths.append(base)
            else:
                dpct = (p - prices[i-1]) / prices[i-1] * 100 if prices[i-1]>0 else 0
                strengths.append(max(min(strengths[-1] + dpct*1.5, 100), 0))
        reasons.append("strength_proxy=price_momentum")

    now_strength = strengths[-1]
    ts_pairs = [(b.ts, s) for b,s in zip([b.ts for b in bars], strengths)]
    slope = _slope(ts_pairs[-min(len(ts_pairs), 12):])   # 取最近 12 點計算斜率
    ema_d = _ema_delta(strengths[-min(len(strengths), 6):], alpha=0.6)
    vr = _vol_ratio(bars)
    reasons += [f"now={now_strength:.1f}", f"slope/h={slope:.2f}", f"emaΔ={ema_d:.2f}", f"vol_ratio={vr:.2f}"]

    # 分類規則
    if (now_strength >= th_long and slope >= DEFAULTS["MIN_SLOPE_FIRE"] and vr >= DEFAULTS["VOL_BOOST_FIRE"]):
        return TrendResult("FIRE","🔥","主升浪：可做多，建議延長監控", reasons)

    if ((th_long-5) <= now_strength < th_long and slope >= DEFAULTS["MIN_SLOPE_BOLT"] and vr >= 1.0) \
       or (now_strength >= th_long and DEFAULTS["MIN_SLOPE_BOLT"] <= slope < DEFAULTS["MIN_SLOPE_FIRE"]):
        return TrendResult("BOLT","⚡","接棒上攻：密切監控，時機可切入", reasons)

    if (now_strength >= (th_long-8) and (slope <= -3.0 or vr <= DEFAULTS["VOL_WEAK_MOON"])):
        return TrendResult("MOON","🌙","轉弱背離：別追高，考慮停利", reasons)

    return TrendResult("IDLE","💤","觀望中性：先看戲不出手", reasons)
