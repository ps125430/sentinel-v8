from fastapi import FastAPI, Request
from datetime import datetime
import os, logging
from zoneinfo import ZoneInfo

# 可選：LINE 推播（若沒填 token 就會自動跳過）
from linebot import LineBotApi
from linebot.models import TextSendMessage

logger = logging.getLogger("uvicorn.error")

TZ = ZoneInfo("Asia/Taipei")
app = FastAPI(title="sentinel-v8")

LINE_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_SECRET = os.getenv("LINE_CHANNEL_SECRET", "")
line_api = LineBotApi(LINE_TOKEN) if LINE_TOKEN else None

# --- 基本健康檢查 ---
@app.get("/", include_in_schema=False)
def root():
    return {
        "status": "ok",
        "message": "sentinel-v8 is live",
        "ts": datetime.now(TZ).isoformat(timespec="seconds"),
    }

@app.get("/healthz")
def healthz():
    return {"ok": True}

# --- 報表骨架（等你給指揮規格後填入真資料） ---
@app.get("/report")
def report(type: str):
    if type not in {"morning", "noon", "evening", "night"}:
        return {"ok": False, "error": "invalid type"}
    payload = {
        "ok": True,
        "type": type,
        "generated_at": datetime.now(TZ).isoformat(timespec="seconds"),
        "top_news": [],
        "long": [],
        "short": [],
    }
    return payload

# --- 管理端：推播（現在先回 200；填好 LINE token 後即可生效） ---
@app.post("/admin/push-report")
async def push_report(type: str, to: str = os.getenv("LINE_DEFAULT_TO", "")):
    # 產生簡訊內容（先用簡短模板；之後依 COMMAND_BRIEF.md 擴充）
    msg = f"【{type}報】sentinel-v8 {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}"
    if line_api and to:
        try:
            line_api.push_message(to, TextSendMessage(text=msg))
            return {"ok": True, "pushed": True, "to": to}
        except Exception as e:
            logger.exception("line push failed: %s", e)
            return {"ok": True, "pushed": False, "error": str(e)}
    return {"ok": True, "pushed": False, "reason": "LINE token or target not set"}

# --- LINE Webhook 佔位（在 LINE Developers 設為 https://<你的域名>/line/webhook） ---
@app.post("/line/webhook")
async def line_webhook(req: Request):
    # 先吃掉 payload；等你確認 token 後我加簽章驗證與指令解析
    _ = await req.body()
    return {"ok": True, "handled": False}

# --- 四時段排程（APScheduler） ---
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

scheduler = BackgroundScheduler(timezone=TZ)

def _job(label: str):
    # 之後會改成真實產報、計分、推播
    logger.info("[job] %s report tick", label)

@app.on_event("startup")
def _start_scheduler():
    # 09:30 / 12:30 / 18:00 / 22:30（台灣時間）
    scheduler.add_job(lambda: _job("morning"),  CronTrigger(hour=9,  minute=30))
    scheduler.add_job(lambda: _job("noon"),     CronTrigger(hour=12, minute=30))
    scheduler.add_job(lambda: _job("evening"),  CronTrigger(hour=18, minute=0))
    scheduler.add_job(lambda: _job("night"),    CronTrigger(hour=22, minute=30))
    scheduler.start()
    logger.info("[scheduler] four-phase reports registered")
