from fastapi import FastAPI, Request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os, json, logging, re, math
import httpx
from typing import List, Dict, Tuple, Optional

# LINE
from linebot import LineBotApi
from linebot.models import TextSendMessage

# Scheduler
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger("uvicorn.error")
TZ = ZoneInfo("Asia/Taipei")

app = FastAPI(title="sentinel-v8")

# -------- ENV & 預設（中性風格） --------
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_DEFAULT_TO = os.getenv("LINE_DEFAULT_TO", "")
line_api = LineBotApi(LINE_TOKEN) if LINE_TOKEN else None

# 觀察名單（CoinGecko 的 id）
WATCHLIST_CRYPTOS = [s.strip() for s in os.getenv(
    "WATCHLIST_CRYPTOS",
    "bitcoin,ethereum,solana,dogecoin,cardano,ripple,chainlink,wrapped-bitcoin,polkadot,matic-network,avalanche-2,uniswap"
).split(",") if s.strip()]

# 分數權重與門檻（中性）
W_STRONG = float(os.getenv("W_STRONG", "0.60"))
W_NEWS   = float(os.getenv("W_NEWS", "0.40"))
TH_LONG  = int(os.getenv("TH_LONG", "70"))  # 做多門檻
TH_SHORT = int(os.getenv("TH_SHORT", "65"))  # 做空門檻

# 是否在強弱清單中加入「建議」標示（✅）
AUTO_SUGGEST = int(os.getenv("AUTO_SUGGEST", "1"))  # 1 開啟（預設），0 關閉

def now_tz() -> datetime:
    return datetime.now(TZ)

def push_text(text: str, to: str | None = None) -> Dict:
    if not line_api:
        return {"sent": False, "reason": "LINE token not set"}
    target = to or LINE_DEFAULT_TO
    if not target:
        return {"sent": False, "reason": "LINE_DEFAULT_TO not set"}
    try:
        line_api.push_message(target, TextSendMessage(text=text))
        return {"sent": True, "to": target}
    except Exception as e:
        logger.exception("LINE push failed: %s", e)
        return {"sent": False, "error": str(e)}

# ------------------ 市場數據：CoinGecko → Binance 容錯 ------------------
CG_BASE = "https://api.coingecko.com/api/v3"
BINANCE_MAP = {
    "bitcoin": "BTCUSDT",
    "ethereum": "ETHUSDT",
    "solana": "SOLUSDT",
    "dogecoin": "DOGEUSDT",
    "cardano": "ADAUSDT",
    "ripple": "XRPUSDT",
    "chainlink": "LINKUSDT",
    "avalanche-2": "AVAXUSDT",
    "polkadot": "DOTUSDT",
    "matic-network": "MATICUSDT",
    "uniswap": "UNIUSDT",
    "wrapped-bitcoin": "BTCUSDT",
}

async def fetch_coingecko_markets(ids: List[str]) -> List[Dict]:
    if not ids: return []
    url = f"{CG_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ",".join(ids),
        "order": "market_cap_desc",
        "per_page": len(ids),
        "page": 1,
        "price_change_percentage": "1h,24h"
    }
    headers = {"User-Agent": "sentinel-v8/1.0"}
    try:
        async with httpx.AsyncClient(timeout=20.0, headers=headers) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            data = r.json()
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("coingecko failed: %s", e)
        return []

async def fetch_binance_markets(ids: List[str]) -> List[Dict]:
    if not ids: return []
    symbols = [BINANCE_MAP[sid] for sid in ids if sid in BINANCE_MAP]
    if not symbols: return []
    url = "https://api.binance.com/api/v3/ticker/24hr"
    try:
        rows = []
        async with httpx.AsyncClient(timeout=20.0) as client:
            for sym in symbols:
                r = await client.get(url, params={"symbol": sym})
                r.raise_for_status()
                j = r.json()
                price = float(j.get("lastPrice", 0.0))
                chg_pct = float(j.get("priceChangePercent", 0.0))
                vol = float(j.get("quoteVolume", 0.0))
                cid = next((k for k, v in BINANCE_MAP.items() if v == sym), sym.lower())
                rows.append({
                    "id": cid,
                    "symbol": sym.replace("USDT", ""),
                    "name": cid,
                    "current_price": price,
                    "price_change_percentage_24h": chg_pct,
                    "total_volume": vol,
                })
        return rows
    except Exception as e:
        logger.warning("binance failed: %s", e)
        return []

async def fetch_markets(ids: List[str]) -> List[Dict]:
    data = await fetch_coingecko_markets(ids)
    return data if data else await fetch_binance_markets(ids)

def normalize(x: float, lo: float, hi: float) -> float:
    if hi <= lo: return 0.5
    v = (x - lo) / (hi - lo)
    return max(0.0, min(1.0, v))

def score_strong(rows: List[Dict]) -> List[Dict]:
    if not rows: return []
    chg_vals = [float(row.get("price_change_percentage_24h") or 0.0) for row in rows]
    vol_vals = [float(row.get("total_volume") or 0.0) for row in rows]
    lo_chg, hi_chg = min(chg_vals), max(chg_vals)
    lo_vol, hi_vol = min(vol_vals), max(vol_vals)

    out = []
    for row in rows:
        sym = (row.get("symbol") or "").upper()
        name = row.get("name") or ""
        chg = float(row.get("price_change_percentage_24h") or 0.0)
        vol = float(row.get("total_volume") or 0.0)
        price = float(row.get("current_price") or 0.0)
        s_chg = normalize(chg, lo_chg, hi_chg)
        s_vol = normalize(math.log1p(vol), math.log1p(lo_vol), math.log1p(hi_vol))
        s = (s_chg * 0.6 + s_vol * 0.4) * 100
        out.append({
            "id": row.get("id"),
            "symbol": sym,
            "name": name,
            "price": price,
            "chg24h": chg,
            "volume": vol,
            "score_strong": round(s, 1),
            "score_news": 0.0,
        })
    return out

def total_score(s_strong: float, s_news: float = 0.0) -> int:
    return int(round(W_STRONG * s_strong + W_NEWS * s_news))

def split_long_short(rows: List[Dict]) -> Tuple[List[Dict], List[Dict]]:
    L, S = [], []
    for r in rows:
        tot = total_score(r["score_strong"], r["score_news"])
        item = {**r, "score_total": tot}
        if tot >= TH_LONG: L.append(item)
        elif tot >= TH_SHORT: S.append(item)
    L.sort(key=lambda x: x["score_total"], reverse=True)
    S.sort(key=lambda x: x["score_total"], reverse=True)
    return L[:5], S[:5]

# ------- 文案（A：行情氣氛派）＋建議標示 -------
def _fmt_row_with_suggest(item: Dict, side_hint: Optional[str] = None) -> str:
    sym = item["symbol"]
    score = item["score_total"]
    if AUTO_SUGGEST:
        if score >= TH_LONG:
            return f"{sym}({score}) ✅ 建議做多"
        if score >= TH_SHORT and (side_hint == "short" or side_hint is None):
            return f"{sym}({score}) ✅ 建議做空"
    return f"{sym}({score})"

def render_digest(phase: str, L, S, news):
    lt = "、".join([_fmt_row_with_suggest(x, "long") for x in L]) or "—"
    st = "、".join([_fmt_row_with_suggest(x, "short") for x in S]) or "—"
    return (
        f"【{phase}報】{now_tz().strftime('%Y-%m-%d %H:%M')}\n"
        f"🚀 做多候選：{lt}\n"
        f"🧊 做空候選：{st}\n"
        f"(中性模式｜多≥{TH_LONG}、空≥{TH_SHORT}｜強度 {int(W_STRONG*100)}%)"
    )

# ------------------ 口令＋監控 ------------------
tasks = {}
cmd_long  = re.compile(r"^\s*([A-Za-z0-9_\-./]+)\s*(做多|多|long)\s*$")
cmd_short = re.compile(r"^\s*([A-Za-z0-9_\-./]+)\s*(做空|空|short)\s*$")
cmd_plus  = re.compile(r"^\s*([A-Za-z0-9_\-./]+)\s*\+\s*$")
cmd_stop  = re.compile(r"^\s*([A-Za-z0-9_\-./]+)\s*-\s*$")

def status_list():
    now = now_tz()
    items = []
    for s, v in tasks.items():
        left = int((v["until"] - now).total_seconds() // 60)
        items.append(f"{s} {v['side']}（剩 {max(left,0)} 分）")
    return "、".join(items) if items else "（無監控）"

scheduler = BackgroundScheduler(timezone=TZ)

def schedule_end(symbol: str):
    job = tasks.get(symbol)
    if not job: return
    until: datetime = job["until"]
    remind_at = until - timedelta(minutes=5)
    if remind_at > now_tz():
        scheduler.add_job(lambda s=symbol: push_text(f"⏰ {s} 任務將在 5 分鐘後到期"), DateTrigger(run_date=remind_at))
    def _expire(s=symbol):
        if s in tasks:
            side = tasks[s]["side"]
            tasks.pop(s, None)
            push_text(f"✅ {s} {side} 監控已到期並結束")
    scheduler.add_job(_expire, DateTrigger(run_date=until))

def create_or_extend(symbol: str, side: str, owner: str):
    n = now_tz()
    if symbol in tasks:
        tasks[symbol]["until"] += timedelta(hours=1)
        until = tasks[symbol]["until"]
        push_text(f"➕ 已延長 {symbol} {tasks[symbol]['side']} 監控至 {until.strftime('%H:%M')}")
    else:
        until = n + timedelta(hours=1)
        tasks[symbol] = {"side": side, "until": until, "owner": owner}
        push_text(f"🟢 已建立 {symbol} {side} 監控，至 {until.strftime('%H:%M')}（到期前 5 分鐘提醒）")
    schedule_end(symbol)

def stop_task(symbol: str):
    if symbol in tasks:
        side = tasks[symbol]["side"]
        tasks.pop(symbol, None)
        push_text(f"🛑 已停止 {symbol} {side} 監控")
    else:
        push_text(f"ℹ️ {symbol} 目前沒有進行中的監控")

# ------- 「今日強勢 / 今日弱勢」：加上建議標示 -------
async def today_strength(msg: str):
    mkt = await fetch_markets(WATCHLIST_CRYPTOS)
    rows = score_strong(mkt)
    # 補總分用於判斷
    for r in rows:
        r["score_total"] = total_score(r["score_strong"], r["score_news"])
    strong_sorted = sorted(rows, key=lambda x: x["score_strong"], reverse=True)
    weak_sorted   = list(reversed(strong_sorted))
    top3_strong = strong_sorted[:3]
    top3_weak   = weak_sorted[:3]

    if "弱" in msg:
        lines = [f"{i+1}. {_fmt_row_with_suggest(x, 'short')}" for i, x in enumerate(top3_weak)]
        text = "🧊 今日弱勢\n" + "\n".join(lines)
    else:
        lines = [f"{i+1}. {_fmt_row_with_suggest(x, 'long')}" for i, x in enumerate(top3_strong)]
        text = "🚀 今日強勢\n" + "\n".join(lines)

    push_text(text if text.strip() else "（目前資料暫無，稍後再試）")

def help_text() -> str:
    return ("指令例：\n"
            "BTC 做多｜ETH 做空｜BTC +（延長1小時）｜ETH -（停止）｜總覽\n"
            "今日強勢｜今日弱勢")

def handle_command_sync(text: str, owner: str):
    t = text.strip()
    if t in {"總覽","狀態","status"}:
        push_text(f"📋 監控：{status_list()}"); return "ok"
    m = cmd_long.match(t)
    if m: create_or_extend(m.group(1).upper(), "做多", owner); return "ok"
    m = cmd_short.match(t)
    if m: create_or_extend(m.group(1).upper(), "做空", owner); return "ok"
    m = cmd_plus.match(t)
    if m:
        sym = m.group(1).upper()
        if sym in tasks: create_or_extend(sym, tasks[sym]["side"], owner)
        else: push_text(f"ℹ️ {sym} 尚未建立監控，可用『{sym} 做多』或『{sym} 做空』")
        return "ok"
    m = cmd_stop.match(t)
    if m: stop_task(m.group(1).upper()); return "ok"
    return "async-needed" if t in {"今日強勢","今日弱勢"} else "help"

# ------------------ 路由 ------------------
@app.get("/", include_in_schema=False)
def root():
    return {"status":"ok","message":"sentinel-v8 is live","time": now_tz().isoformat(timespec="seconds")}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/report")
async def report(type: str, raw: int = 0):
    if type not in {"morning","noon","evening","night"}:
        return {"ok": False, "error": "invalid type"}
    mkt = await fetch_markets(WATCHLIST_CRYPTOS)
    rows = score_strong(mkt)
    # 加總分
    for r in rows:
        r["score_total"] = total_score(r["score_strong"], r["score_news"])
    L, S = split_long_short(rows)
    resp = {
        "ok": True,
        "type": type,
        "generated_at": now_tz().isoformat(timespec="seconds"),
        "watchlist": WATCHLIST_CRYPTOS,
        "long": L, "short": S,
        "top_news": []
    }
    if raw:
        resp["raw_strength"] = rows
    return resp

@app.get("/admin/push")
async def push_alias(type: str):
    mkt = await fetch_markets(WATCHLIST_CRYPTOS)
    rows = score_strong(mkt)
    for r in rows:
        r["score_total"] = total_score(r["score_strong"], r["score_news"])
    L, S = split_long_short(rows)
    text = render_digest(type, L, S, news=[])
    res = push_text(text)
    return {"ok": True, **res, "preview": text}

@app.get("/admin/push-report")
async def push_report_get(type: str):
    return await push_alias(type)

@app.post("/admin/push-report")
async def push_report_post(type: str):
    return await push_alias(type)

# Webhook：含「今日強勢 / 今日弱勢」
@app.post("/line/webhook")
async def line_webhook(req: Request):
    body = await req.body()
    try:
        data = json.loads(body.decode("utf-8"))
    except:
        data = {}
    try:
        events = data.get("events", [])
        for ev in events:
            src = ev.get("source", {})
            uid = src.get("userId"); gid = src.get("groupId"); rid = src.get("roomId")
            msg = ev.get("message", {}) or {}
            text = (msg.get("text") or "").strip()
            logger.info("[LINE] src uid=%s gid=%s rid=%s text=%s", uid, gid, rid, text)
            mode = handle_command_sync(text, owner=uid or gid or rid or "")
            if mode == "async-needed":
                await today_strength(text)
            elif mode == "help":
                push_text(help_text())
    except Exception as e:
        logger.exception("Webhook parse error: %s", e)
    return {"ok": True, "handled": True}

# 自動推播排程
def schedule_tick(label: str):
    async def _run():
        try:
            mkt = await fetch_markets(WATCHLIST_CRYPTOS)
            rows = score_strong(mkt)
            for r in rows:
                r["score_total"] = total_score(r["score_strong"], r["score_news"])
            L, S = split_long_short(rows)
            text = render_digest(label, L, S, news=[])
            push_text(text)
        except Exception as e:
            logger.exception("tick failed: %s", e)
    import anyio
    anyio.from_thread.run(anyio.run, _run)

scheduler = BackgroundScheduler(timezone=TZ)

@app.on_event("startup")
def start_scheduler():
    scheduler.add_job(lambda: schedule_tick("morning"), CronTrigger(hour=9,  minute=30))
    scheduler.add_job(lambda: schedule_tick("noon"),    CronTrigger(hour=12, minute=30))
    scheduler.add_job(lambda: schedule_tick("evening"), CronTrigger(hour=18, minute=0))
    scheduler.add_job(lambda: schedule_tick("night"),   CronTrigger(hour=22, minute=30))
    scheduler.start()
    logger.info("[scheduler] four-phase schedule registered")
