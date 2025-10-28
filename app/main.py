from __future__ import annotations
from fastapi import FastAPI, Request
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo
import os, re, time

from app.state_store import get_state, save_state, set_watch, cleanup_expired, list_watches
from app.services.prefs import resolve_scheme, set_color_scheme, current_scheme
from app.services import watches as W
from app import news_scoring

# ==== 可替換為你的 LINE SDK 推播函式 ====
def push_to_line(text: str):
    # TODO: 接上你原本的 LineBotApi push
    print("[LINE]", text)

TZ = ZoneInfo("Asia/Taipei")
app = FastAPI(title="sentinel-v8")

# 啟動：預熱狀態，確保檔案存在
@app.on_event("startup")
def on_startup():
    _ = get_state()
    save_state()

# Webhook：只示意文本解析的主邏輯（保留你原本的簽名驗證/回覆流程）
@app.post("/line/webhook")
async def line_webhook(request: Request):
    payload = await request.json()
    events = payload.get("events", [])
    replies = []
    for ev in events:
        text = (ev.get("message", {}) or {}).get("text", "") or ""
        t = re.sub(r"\s+", " ", text.replace("\u3000", " ")).strip()

        # 顏色 台股／美股
        if t.startswith("顏色"):
            scheme = resolve_scheme(t)
            replies.append(set_color_scheme(scheme) if scheme else "請說明要切換到「台股」或「美股」配色。")
            continue

        # + 延長、- 停止
        sym = W.parse_plus(t)
        if sym:
            replies.append(W.extend(sym, hours=1))
            continue
        sym = W.parse_minus(t)
        if sym:
            replies.append(W.stop(sym))
            continue

        # 總覽／監控查詢
        if t in ("總覽", "監控", "監控列表", "監控清單"):
            replies.append(W.summarize())
            continue

        # 今日強勢／今日弱勢（此處接你的主升浪輸出）
        if t in ("今日強勢", "今日弱勢"):
            # TODO: 接 trend_integrator 的實際結果；示意用配色＋監控
            msg = f"{t}（配色：{current_scheme()}）\n監控：{W.summarize()}\n— 此處接主升浪輸出與分數 —"
            replies.append(msg)
            continue

        # <幣> 做多／做空（示意：設定 1h 監控）
        m = re.match(r"^\s*([A-Za-z0-9_\-\.]+)\s*(做多|做空)\s*$", t)
        if m:
            sym, action = m.group(1).upper(), m.group(2)
            set_watch(sym, int(time.time()) + 3600)
            replies.append(f"{sym} 設定為{action}，並已監控 1 小時。")
            continue

    return {"messages": replies}

# ====== 報表與提醒排程 ======
sched = BackgroundScheduler(timezone=str(TZ))

def compose_report(phase: str) -> str:
    scheme = current_scheme()
    watches_snapshot = W.summarize()
    # 這裡可插入你的主升浪結果與新聞分數
    # 例：symbols = ["BTC","ETH","SOL"]; scores = news_scoring.batch_news_score(symbols)
    return f"【{phase}報】配色：{scheme}\n監控：{watches_snapshot}\n— 主升浪＆新聞分數待接 —"

@sched.scheduled_job("cron", hour=9, minute=30)
def phase_morning():
    push_to_line(compose_report("morning"))

@sched.scheduled_job("cron", hour=12, minute=30)
def phase_noon():
    push_to_line(compose_report("noon"))

@sched.scheduled_job("cron", hour=18, minute=0)
def phase_evening():
    push_to_line(compose_report("evening"))

@sched.scheduled_job("cron", hour=22, minute=30)
def phase_night():
    push_to_line(compose_report("night"))

# 每分鐘：清過期 + 5 分鐘到期提醒
@sched.scheduled_job("cron", second=10)  # 每分鐘的 10 秒點跑一次，避開 00 秒擁擠
def watch_keeper():
    now = int(time.time())
    ws = list_watches()
    # 5 分鐘內到期提醒（每標的一次）
    for sym, v in ws.items():
        until = int(v.get("until", 0))
        last = int(v.get("last_alert", 0))
        remain = until - now
        if 0 < remain <= 300 and last < (until - 300):  # 還未提醒過
            push_to_line(f"⏰ {sym} 監控將於 {remain//60} 分後到期（{time.strftime('%H:%M', time.localtime(until))}）")
            # 記錄已提醒
            v["last_alert"] = now
    # 清理過期
    cleanup_expired(now)

@app.on_event("startup")
def start_sched():
    if not sched.running:
        sched.start()
