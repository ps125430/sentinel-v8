from fastapi import FastAPI, Request
from datetime import datetime
from zoneinfo import ZoneInfo
import os, json, logging

# --- LINE 推播 (token 可留空，之後補也能跑) ---
from linebot import LineBotApi
from linebot.models import TextSendMessage

logger = logging.getLogger("uvicorn.error")
TZ = ZoneInfo("Asia/Taipei")

app = FastAPI(title="sentinel-v8")

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
LINE_DEFAULT_TO = os.getenv("LINE_DEFAULT_TO", "")
line_api = LineBotApi(LINE_TOKEN) if LINE_TOKEN else None


# ---------------------------
#   基本狀態檢查
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


# ---------------------------
#   報表骨架（之後依指揮規格填資料）
# ---------------------------
@app.get("/report")
def report(type: str):
    if type not in {"morning", "noon", "evening", "night"}:
        return {"ok": False, "error": "invalid type"}

    return {
        "ok": True,
        "type": type,
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "top_news": [],
        "long": [],
        "short": []
    }


# ---------------------------
#   推播（GET & POST 都可；另開 GET 別名 /admin/push）
# ---------------------------
def _push_text(text: str):
    if line_api and LINE_DEFAULT_TO:
        try:
            line_api.push_message(LINE_DEFAULT_TO, TextSendMessage(text=text))
            return {"sent": True, "to": LINE_DEFAULT_TO}
        except Exception as e:
            logger.exception("LINE push failed: %s", e)
            return {"sent": False, "error": str(e)}
    return {"sent": False, "reason": "LINE not configured"}

@app.get("/admin/push")
async def push_alias(type: str):
    text = f"【{type}報】sentinel-v8 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"
    res = _push_text(text)
    return {"ok": True, **res, "preview": text}

@app.get("/admin/push-report")
async def push_report_get(type: str):
    text = f"【{type}報】sentinel-v8 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"
    res = _push_text(text)
    return {"ok": True, **res, "preview": text}

@app.post("/admin/push-report")
async def push_report_post(type: str):
    text = f"【{type}報】sentinel-v8 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"
    res = _push_text(text)
    return {"ok": True, **res, "preview": text}


# ---------------------------
#   LINE Webhook（取得 userId / groupId）
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
    except Exception as e:
        logger.exception("Webhook parse error: %s", e)

    return {"ok": True, "handled": False}


# ---------------------------
#   四階段排程
# ---------------------------
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BackgroundScheduler(timezone=TZ)

def _job(label):
    logger.info("[Job] %s report tick at %s", label, datetime.now(TZ))

@app.on_event("startup")
def start_scheduler():
    scheduler.add_job(lambda: _job("morning"),  CronTrigger(hour=9,  minute=30))
    scheduler.add_job(lambda: _job("noon"),     CronTrigger(hour=12, minute=30))
    scheduler.add_job(lambda: _job("evening"),  CronTrigger(hour=18, minute=0))
    scheduler.add_job(lambda: _job("night"),    CronTrigger(hour=22, minute=30))
    scheduler.start()
    logger.info("[scheduler] four-phase schedule registered")
