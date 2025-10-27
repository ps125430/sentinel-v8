from fastapi import FastAPI, Request
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os, json, logging, re

# --- LINE 推播 (token 可留空，之後補也能跑) ---
from linebot import LineBotApi
from linebot.models import TextSendMessage

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger

logger = logging.getLogger("uvicorn.error")
TZ = ZoneInfo("Asia/Taipei")

app = FastAPI(title="sentinel-v8")

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_DEFAULT_TO = os.getenv("LINE_DEFAULT_TO", "")  # Ua8b2f... 或群組 C...
line_api = LineBotApi(LINE_TOKEN) if LINE_TOKEN else None

# ---------------------------
#   In-memory 監控工作表
#   tasks[symbol] = {"side":"long/short","until":datetime,"owner":"userId"}
# ---------------------------
tasks = {}

def push_text(text: str, to: str | None = None):
    """安全推播（沒設定 token/對象時不會爆）"""
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

# ---------------------------
#   基本健康與報表骨架
# ---------------------------
@app.get("/", include_in_schema=False)
def root():
    return {
        "status": "ok",
        "message": "sentinel-v8 is live",
        "time": datetime.now(TZ).isoformat(timespec="seconds")
    }

@app.get("/healthz")
def healthz():
    return {"ok": True}

@app.get("/report")
def report(type: str):
    if type not in {"morning", "noon", "evening", "night"}:
        return {"ok": False, "error": "invalid type"}
    # 之後依 COMMAND_BRIEF.md 填入真資料
    return {
        "ok": True,
        "type": type,
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "top_news": [],
        "long": [],
        "short": []
    }

# ---------------------------
#   四階段排程（09:30 / 12:30 / 18:00 / 22:30）
# ---------------------------
scheduler = BackgroundScheduler(timezone=TZ)

def _tick(label: str):
    logger.info("[Job] %s report tick at %s", label, datetime.now(TZ))
    # 之後改為真正產報＋推播
    push_text(f"【{label}報】tick {datetime.now(TZ).strftime('%H:%M')}")

@app.on_event("startup")
def start_scheduler():
    scheduler.add_job(lambda: _tick("morning"), CronTrigger(hour=9,  minute=30))
    scheduler.add_job(lambda: _tick("noon"),    CronTrigger(hour=12, minute=30))
    scheduler.add_job(lambda: _tick("evening"), CronTrigger(hour=18, minute=0))
    scheduler.add_job(lambda: _tick("night"),   CronTrigger(hour=22, minute=30))
    scheduler.start()
    logger.info("[scheduler] four-phase schedule registered")

# ---------------------------
#   監控任務：建立 / 延長 / 停止 / 提醒
# ---------------------------
def schedule_end(symbol: str):
    """建立到期提醒（到期前 5 分鐘提醒一次，並在到期時結束通知）"""
    job = tasks.get(symbol)
    if not job:
        return
    until: datetime = job["until"]

    # 到期前 5 分鐘提醒
    remind_at = until - timedelta(minutes=5)
    if remind_at > datetime.now(TZ):
        scheduler.add_job(
            lambda s=symbol: push_text(f"⏰ {s} 任務將在 5 分鐘後到期"),
            DateTrigger(run_date=remind_at)
        )
    # 到期通知 + 移除
    def _expire(s=symbol):
        if s in tasks:
            side = tasks[s]["side"]
            tasks.pop(s, None)
            push_text(f"✅ {s} {side} 監控已到期並結束")
    scheduler.add_job(_expire, DateTrigger(run_date=until))

def create_or_extend(symbol: str, side: str, owner: str):
    now = datetime.now(TZ)
    if symbol in tasks:
        # 延長 1 小時
        tasks[symbol]["until"] += timedelta(hours=1)
        until = tasks[symbol]["until"]
        push_text(f"➕ 已延長 {symbol} {tasks[symbol]['side']} 監控至 {until.strftime('%H:%M')}")
    else:
        until = now + timedelta(hours=1)
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

def status_list():
    now = datetime.now(TZ)
    items = []
    for s, v in tasks.items():
        left = int((v["until"] - now).total_seconds() // 60)
        items.append(f"{s} {v['side']}（剩 {max(left,0)} 分）")
    return "、".join(items) if items else "（無監控）"

# ---------------------------
#   口令解析（繁中）
#   範例：
#     BTC 做多      → 建立 BTC long（1 小時）
#     ETH 做空      → 建立 ETH short（1 小時）
#     BTC +         → 延長 BTC 1 小時
#     ETH -         → 停止 ETH 監控
#     總覽          → 列出所有監控
# ---------------------------
cmd_long  = re.compile(r"^\s*([A-Za-z0-9_\-./]+)\s*(做多|多|long)\s*$")
cmd_short = re.compile(r"^\s*([A-Za-z0-9_\-./]+)\s*(做空|空|short)\s*$")
cmd_plus  = re.compile(r"^\s*([A-Za-z0-9_\-./]+)\s*\+\s*$")
cmd_stop  = re.compile(r"^\s*([A-Za-z0-9_\-./]+)\s*-\s*$")

def handle_command(text: str, owner: str):
    t = text.strip()
    if t in {"總覽","狀態","status"}:
        push_text(f"📋 監控：{status_list()}")
        return

    m = cmd_long.match(t)
    if m:
        sym = m.group(1).upper()
        create_or_extend(sym, "做多", owner)
        return

    m = cmd_short.match(t)
    if m:
        sym = m.group(1).upper()
        create_or_extend(sym, "做空", owner)
        return

    m = cmd_plus.match(t)
    if m:
        sym = m.group(1).upper()
        if sym in tasks:
            create_or_extend(sym, tasks[sym]["side"], owner)
        else:
            push_text(f"ℹ️ {sym} 尚未建立監控，可用『{sym} 做多』或『{sym} 做空』")
        return

    m = cmd_stop.match(t)
    if m:
        sym = m.group(1).upper()
        stop_task(sym)
        return

    # 幫助提示
    push_text("指令例：\nBTC 做多｜ETH 做空｜BTC +（延長1小時）｜ETH -（停止）｜總覽")

# ---------------------------
#   LINE Webhook（取得 userId / groupId；處理訊息指令）
# ---------------------------
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
            uid = src.get("userId")
            gid = src.get("groupId")
            rid = src.get("roomId")
            logger.info("[LINE] source -> userId=%s groupId=%s roomId=%s", uid, gid, rid)

            m = ev.get("message", {})
            text = m.get("text")
            if text:
                handle_command(text, owner=uid or gid or rid or "")
    except Exception as e:
        logger.exception("Webhook parse error: %s", e)

    return {"ok": True, "handled": True}

# ---------------------------
#   管理端推播（GET/POST 皆可）
# ---------------------------
def _render_title(label: str):
    return f"【{label}報】sentinel-v8 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"

@app.get("/admin/push")
async def push_alias(type: str):
    text = _render_title(type)
    res = push_text(text)
    return {"ok": True, **res, "preview": text}

@app.get("/admin/push-report")
async def push_report_get(type: str):
    text = _render_title(type)
    res = push_text(text)
    return {"ok": True, **res, "preview": text}

@app.post("/admin/push-report")
async def push_report_post(type: str):
    text = _render_title(type)
    res = push_text(text)
    return {"ok": True, **res, "preview": text}
