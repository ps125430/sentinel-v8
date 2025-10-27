from fastapi import FastAPI, Request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os, json, logging, re, math
import httpx

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

# -------- ENV & 預設（可不填也能跑） --------
LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_DEFAULT_TO = os.getenv("LINE_DEFAULT_TO", "")  # Ua8b2f... or C...
line_api = LineBotApi(LINE_TOKEN) if LINE_TOKEN else None

# 預設觀察名單（逗號分隔）。可在 Render 的 Environment 改：WATCHLIST_CRYPTOS=bitcoin,ethereum,solana,binancecoin
WATCHLIST_CRYPTOS = [s.strip() for s in os.getenv("WATCHLIST_CRYPTOS", "bitcoin,ethereum,solana,binancecoin").split(",") if s.strip()]

# 預設權重與門檻（你說要用預設版）
W_NEWS = float(os.getenv("W_NEWS", "0.40"))         # 40%
W_STRONG = float(os.getenv("W_STRONG", "0.60"))     # 60%
TH_LONG = int(os.getenv("TH_LONG", "75"))           # 做多門檻
TH_SHORT = int(os.getenv("TH_SHORT", "70"))         # 做空門檻
UNICORN_TH = int(os.getenv("UNICORN_TH", "80"))     # Unicorn ≥80（跨天邏輯之後接資料庫）

# =============== 工具 ===============
def now_tz():
    return datetime.now(TZ)

def push_text(text: str, to: str | None = None):
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

# =============== 數據抓取（無金鑰版：CoinGecko 公開 API） ===============
# 備註：只用於 demo 強弱分數；之後可接 CMC/交易所與你指定權重
CG_BASE = "https://api.coingecko.com/api/v3"

async def fetch_crypto_markets(ids: list[str]) -> list[dict]:
    # id 用 coingecko 的 slug（例：bitcoin, ethereum, solana, binancecoin）
    if not ids:
        return []
    url = f"{CG_BASE}/coins/markets"
    params = {
        "vs_currency": "usd",
        "ids": ",".join(ids),
        "order": "market_cap_desc",
        "per_page": len(ids),
        "page": 1,
        "price_change_percentage": "1h,24h"
    }
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.exception("coingecko failed: %s", e)
        return []

def normalize(x: float, lo: float, hi: float) -> float:
    if hi <= lo: 
        return 0.5
    v = (x - lo) / (hi - lo)
    return max(0.0, min(1.0, v))

def score_strong_for_markets(rows: list[dict]) -> list[dict]:
    # 以 24h 漲跌% 與成交量做簡化強度分數（0~100）
    if not rows:
        return []
    chg_vals = [row.get("price_change_percentage_24h") or 0.0 for row in rows]
    vol_vals = [row.get("total_volume") or 0.0 for row in rows]
    lo_chg, hi_chg = min(chg_vals), max(chg_vals)
    lo_vol, hi_vol = min(vol_vals), max(vol_vals)

    out = []
    for row in rows:
        sym = row.get("symbol","").upper()
        name = row.get("name","")
        chg = float(row.get("price_change_percentage_24h") or 0.0)
        vol = float(row.get("total_volume") or 0.0)

        s_chg = normalize(chg, lo_chg, hi_chg)
        s_vol = normalize(math.log1p(vol), math.log1p(lo_vol), math.log1p(hi_vol))

        score_strong = (s_chg*0.6 + s_vol*0.4) * 100  # 可調：漲跌 60%、量能 40%
        out.append({
            "id": row.get("id"),
            "symbol": sym,
            "name": name,
            "price": row.get("current_price"),
            "chg24h": chg,
            "volume": vol,
            "score_strong": round(score_strong, 1)
        })
    # 之後：Score_news 接上後，總分 = W_NEWS*Score_news + W_STRONG*Score_strong
    return out

def mix_total_score(score_strong: float, score_news: float = 0.0) -> int:
    total = W_STRONG*score_strong + W_NEWS*score_news
    return int(round(total))

def pick_long_short(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    # 以「總分」判定做多/做空門檻
    longs, shorts = [], []
    for r in rows:
        total = mix_total_score(r["score_strong"], 0.0)  # 先無新聞分數
        item = {**r, "score_total": total}
        if total >= TH_LONG:
            longs.append(item)
        elif total >= TH_SHORT:  # 70~74 視為弱中帶空，放在 short 候選
            shorts.append(item)
    # 排序
    longs.sort(key=lambda x: x["score_total"], reverse=True)
    shorts.sort(key=lambda x: x["score_total"], reverse=True)
    return longs[:5], shorts[:5]

# =============== 報表與模板 ===============
def render_template(phase: str, longs: list[dict], shorts: list[dict], top_news: list[str]) -> str:
    lt = "、".join([f"{x['symbol']}({x['score_total']})" for x in longs]) or "—"
    st = "、".join([f"{x['symbol']}({x['score_total']})" for x in shorts]) or "—"
    nt = " | ".join(top_news[:3]) if top_news else "—"
    return (
        f"【{phase}報】{now_tz().strftime('%Y-%m-%d %H:%M')}\n"
        f"🚀 做多候選：{lt}\n"
        f"🧊 做空候選：{st}\n"
        f"📰 熱點：{nt}\n"
        f"（提示：多≥{TH_LONG}、空≥{TH_SHORT}；強度比重 {int(W_STRONG*100)}%）"
    )

# =============== 口令／任務（跟你現有的一樣） ===============
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

def schedule_end(symbol: str):
    job = tasks.get(symbol)
    if not job:
        return
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

def handle_command(text: str, owner: str):
    t = text.strip()
    if t in {"總覽","狀態","status"}:
        push_text(f"📋 監控：{status_list()}"); return
    m = cmd_long.match(t)
    if m: create_or_extend(m.group(1).upper(), "做多", owner); return
    m = cmd_short.match(t)
    if m: create_or_extend(m.group(1).upper(), "做空", owner); return
    m = cmd_plus.match(t)
    if m:
        sym = m.group(1).upper()
        if sym in tasks: create_or_extend(sym, tasks[sym]["side"], owner)
        else: push_text(f"ℹ️ {sym} 尚未建立監控，可用『{sym} 做多』或『{sym} 做空』")
        return
    m = cmd_stop.match(t)
    if m: stop_task(m.group(1).upper()); return
    push_text("指令例：\nBTC 做多｜ETH 做空｜BTC +（延長1小時）｜ETH -（停止）｜總覽")

# =============== 路由：健康／報表／推播／Webhook ===============
@app.get("/", include_in_schema=False)
def root():
    return {"status":"ok","message":"sentinel-v8 is live","time": now_tz().isoformat(timespec="seconds")}

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/report")
async def report(type: str):
    if type not in {"morning","noon","evening","night"}:
        return {"ok": False, "error": "invalid type"}
    mkt = await fetch_crypto_markets(WATCHLIST_CRYPTOS)
    rows = score_strong_for_markets(mkt)
    longs, shorts = pick_long_short(rows)
    return {
        "ok": True,
        "type": type,
        "generated_at": now_tz().isoformat(timespec="seconds"),
        "watchlist": WATCHLIST_CRYPTOS,
        "long": longs,
        "short": shorts,
        "top_news": []  # 之後接新聞
    }

def _title(phase: str): 
    return f"【{phase}報】sentinel-v8 {now_tz().strftime('%Y-%m-%d %H:%M')}"

@app.get("/admin/push")
async def push_alias(type: str):
    # 拉一次資料並用模板推播
    mkt = await fetch_crypto_markets(WATCHLIST_CRYPTOS)
    rows = score_strong_for_markets(mkt)
    longs, shorts = pick_long_short(rows)
    text = render_template(type, longs, shorts, top_news=[])
    res = push_text(text)
    return {"ok": True, **res, "preview": text}

@app.get("/admin/push-report")
async def push_report_get(type: str):
    return await push_alias(type)

@app.post("/admin/push-report")
async def push_report_post(type: str):
    return await push_alias(type)

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
            logger.info("[LINE] source -> userId=%s groupId=%s roomId=%s", uid, gid, rid)
            msg = ev.get("message", {})
            text = msg.get("text")
            if text:
                handle_command(text, owner=uid or gid or rid or "")
    except Exception as e:
        logger.exception("Webhook parse error: %s", e)
    return {"ok": True, "handled": True}

# =============== 四時段排程（自動推播） ===============
scheduler = BackgroundScheduler(timezone=TZ)

def schedule_tick(label: str):
    async def _run():
        try:
            mkt = await fetch_crypto_markets(WATCHLIST_CRYPTOS)
            rows = score_strong_for_markets(mkt)
            longs, shorts = pick_long_short(rows)
            text = render_template(label, longs, shorts, top_news=[])
            push_text(text)
        except Exception as e:
            logger.exception("tick failed: %s", e)
    # 背景調度器不支援直接 await，這裡用同步包裝
    import anyio
    anyio.from_thread.run(anyio.run, _run)

@app.on_event("startup")
def start_scheduler():
    scheduler.add_job(lambda: schedule_tick("morning"), CronTrigger(hour=9,  minute=30))
    scheduler.add_job(lambda: schedule_tick("noon"),    CronTrigger(hour=12, minute=30))
    scheduler.add_job(lambda: schedule_tick("evening"), CronTrigger(hour=18, minute=0))
    scheduler.add_job(lambda: schedule_tick("night"),   CronTrigger(hour=22, minute=30))
    scheduler.start()
    logger.info("[scheduler] four-phase schedule registered")
