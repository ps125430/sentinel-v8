# =========================
# app/main.py 〔覆蓋版・一鍵貼上〕
# Sentinel v8 · FastAPI + APScheduler + LINE Reply + 版本核對/徽章
# =========================

from __future__ import annotations

import os
import re
import time
from zoneinfo import ZoneInfo

from fastapi import FastAPI, Request
from apscheduler.schedulers.background import BackgroundScheduler

# --- Internal modules (既有模組) ---
from app.state_store import get_state, save_state, set_watch, cleanup_expired, list_watches
from app.services.prefs import resolve_scheme, set_color_scheme, current_scheme
from app.services import watches as W
from app import trend_integrator, news_scoring
from app import us_stocks, us_news
from app import badges_radar
from app.services import version_diff

# --- LINE SDK (用於 webhook 回覆 & 定時推播) ---
from linebot import LineBotApi
from linebot.models import TextSendMessage

# ========= 基本設定 =========
TZ = ZoneInfo("Asia/Taipei")
app = FastAPI(title="sentinel-v8")

LINE_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_PUSH_TO = os.getenv("LINE_PUSH_TO", "")  # 可放你的 userId 或 groupId（推播用）
line_bot_api = LineBotApi(LINE_ACCESS_TOKEN) if LINE_ACCESS_TOKEN else None

# ========= Admin Router（版本 API）=========
from app import admin_version
app.include_router(admin_version.router)

# ========= 啟動流程 =========
@app.on_event("startup")
def on_startup():
    # 確保狀態檔存在
    _ = get_state()
    save_state()

    # 啟動先刷新徽章，避免空值
    try:
        badges_radar.refresh_badges()
    except Exception:
        pass

    # 沒有版本基準就自動建立（/tmp/sentinel-v8.version-prev.json）
    try:
        prev_path = "/tmp/sentinel-v8.version-prev.json"
        if not os.path.exists(prev_path):
            version_diff.checkpoint_now(".")
    except Exception:
        pass

# ========= LINE 推播封裝（排程訊息用）=========
def push_to_line(text: str):
    """
    定時任務推播：
    - 若設定 LINE_ACCESS_TOKEN + LINE_PUSH_TO：用 push_message
    - 否則印到 logs（保底）
    """
    if line_bot_api and LINE_PUSH_TO:
        try:
            line_bot_api.push_message(LINE_PUSH_TO, TextSendMessage(text))
            return
        except Exception as e:
            print(f"[LINE push error] {e}")
    print("[LINE]", text)

# ========= LINE Webhook（文字指令處理）— 直接回覆 =========
@app.post("/line/webhook")
async def line_webhook(request: Request):
    payload = await request.json()
    events = payload.get("events", [])
    out = []  # fallback JSON

    for ev in events:
        raw = (ev.get("message", {}) or {}).get("text", "") or ""
        reply_token = ev.get("replyToken")
        t = re.sub(r"\s+", " ", raw.replace("\u3000", " ")).strip()

        # --- 版本核對 / 版本差異（別名全吃；最優先） ---
        if t in ("版本核對", "版本差異", "版本差异", "version diff", "version-diff", "ver diff"):
            try:
                diff = version_diff.diff_now_vs_prev(".")
                msg = diff.get("summary") or "（無法產生摘要）"
            except Exception as e:
                msg = f"版本比對失敗：{e}"
            out.append(msg)
            if line_bot_api and reply_token:
                line_bot_api.reply_message(reply_token, TextSendMessage(msg))
            continue

        # --- 配色切換 ---
        if t.startswith("顏色"):
            scheme = resolve_scheme(t)
            msg = set_color_scheme(scheme) if scheme else "請說明要切換到「台股」或「美股」配色。"
            out.append(msg)
            if line_bot_api and reply_token:
                line_bot_api.reply_message(reply_token, TextSendMessage(msg))
            continue

        # --- 新聞 <幣> ---
        m_news = re.match(r"^\s*新聞\s+([A-Za-z0-9_\-\.]+)\s*$", t)
        if m_news:
            sym = m_news.group(1).upper()
            heads = news_scoring.recent_headlines(sym, k=5)
            if not heads:
                msg = f"{sym} 近 24 小時無新聞或暫時無法取得。"
            else:
                lines = [f"🗞️ {sym} 近 24 小時重點新聞（中文）"]
                for i, h in enumerate(heads, 1):
                    lines.append(f"{i}. {h['title_zh']} 〔{h['timeago']}〕")
                msg = "\n".join(lines)
            out.append(msg)
            if line_bot_api and reply_token:
                line_bot_api.reply_message(reply_token, TextSendMessage(msg))
            continue

        # --- 美股（詳細：逐檔一行 + 中文新聞重點） ---
        if t == "美股":
            block = us_stocks.format_us_full()
            nblk = us_news.format_us_news_block(k_each=2, max_topics=6)
            msg = f"{block}\n\n{nblk}"
            out.append(msg)
            if line_bot_api and reply_token:
                line_bot_api.reply_message(reply_token, TextSendMessage(msg))
            continue

        # --- 監控延長 + / 停止 - ---
        sym = W.parse_plus(t)
        if sym:
            msg = W.extend(sym, hours=1)
            out.append(msg)
            if line_bot_api and reply_token:
                line_bot_api.reply_message(reply_token, TextSendMessage(msg))
            continue

        sym = W.parse_minus(t)
        if sym:
            msg = W.stop(sym)
            out.append(msg)
            if line_bot_api and reply_token:
                line_bot_api.reply_message(reply_token, TextSendMessage(msg))
            continue

        # --- 總覽 ---
        if t in ("總覽", "監控", "監控列表", "監控清單"):
            msg = W.summarize()
            out.append(msg)
            if line_bot_api and reply_token:
                line_bot_api.reply_message(reply_token, TextSendMessage(msg))
            continue

        # --- 今日強勢 / 今日弱勢（含中文新聞精選） ---
        if t in ("今日強勢", "今日弱勢"):
            scheme = current_scheme()
            want_strong = (t == "今日強勢")
            try:
                msg = trend_integrator.generate_side(single=t, scheme=scheme, want_strong=want_strong, topn=3)
                # 抽取上榜幣，附 2 則新聞
                syms = []
                for line in msg.splitlines():
                    m = re.search(r"\b([A-Z]{2,10})\b", line)
                    if m:
                        s = m.group(1)
                        if s not in ("S", "N", "T"):
                            syms.append(s)
                syms = [s for s in syms if s.isalpha()]
                hmap = news_scoring.batch_recent_headlines(syms, k=2) if syms else {}
                if hmap:
                    msg += "\n\n🗞️ 中文新聞精選"
                    for s in syms:
                        heads = hmap.get(s) or []
                        if not heads:
                            continue
                        msg += f"\n• {s}"
                        for h in heads:
                            msg += f"\n  - {h['title_zh']} 〔{h['timeago']}〕"
            except Exception as e:
                msg = f"{t} 生成失敗：{e}\n（稍後重試或檢查外網）"
            out.append(msg)
            if line_bot_api and reply_token:
                line_bot_api.reply_message(reply_token, TextSendMessage(msg))
            continue

        # --- <幣> 做多/做空：建立 1h 監控 ---
        m = re.match(r"^\s*([A-Za-z0-9_\-\.]+)\s*(做多|做空)\s*$", t)
        if m:
            sym, action = m.group(1).upper(), m.group(2)
            set_watch(sym, int(time.time()) + 3600)
            msg = f"{sym} 設定為{action}，並已監控 1 小時。"
            out.append(msg)
            if line_bot_api and reply_token:
                line_bot_api.reply_message(reply_token, TextSendMessage(msg))
            continue

        # --- 預設回覆 ---
        msg = "指令：今日強勢｜今日弱勢｜美股｜新聞 <幣>｜顏色 台股/美股｜總覽｜版本核對｜版本差異"
        out.append(msg)
        if line_bot_api and reply_token:
            line_bot_api.reply_message(reply_token, TextSendMessage(msg))

    # 不走 LINE（測試/CI）時，回 JSON 看結果
    return {"messages": out}

# ========= 報表（四時段）=========
def compose_report(phase: str) -> str:
    scheme = current_scheme()

    # 收集徽章
    badges = []
    try:
        badges = badges_radar.get_badges()
    except Exception:
        badges = []

    # 版本徽章（即使 badges_radar 未掛也能顯示）
    try:
        has_delta, badge_txt = version_diff.get_version_badge()
        if has_delta and badge_txt not in badges:
            badges.append(badge_txt)
    except Exception:
        pass

    badge_str = (" ｜ " + " ".join(f"[{b}]" for b in badges)) if badges else ""

    # Crypto 主升浪
    try:
        ti = trend_integrator.generate_report(scheme=scheme, topn=3)
    except Exception as e:
        ti = f"主升浪清單生成失敗：{e}"

    # 美股區塊：夜報=開盤雷達+新聞；早報=隔夜回顧；其餘略
    us_block = ""
    if phase == "night":
        us_block = us_stocks.format_us_block(phase="night")
        us_news_block = us_news.format_us_news_block(k_each=2, max_topics=6)
        us_block = f"{us_block}\n\n{us_news_block}"
    elif phase == "morning":
        us_block = us_stocks.format_us_block(phase="morning")

    watches_snapshot = W.summarize()
    header = f"【{phase}報】配色：{scheme}{badge_str}"

    parts = [header, f"監控：{watches_snapshot}", ""]
    if us_block:
        parts.append(us_block)
        parts.append("")
    parts.append(ti)
    return "\n".join(parts)

# ========= 排程（四報 + 徽章刷新 + 每分鐘提醒/清理）=========
sched = BackgroundScheduler(timezone=str(TZ))

def _safe_compose(phase: str) -> str:
    try:
        return compose_report(phase)
    except Exception as e:
        return f"【{phase}報】生成失敗：{e}"

@sched.scheduled_job("cron", hour=9, minute=30)   # 09:30 早報
def phase_morning(): push_to_line(_safe_compose("morning"))

@sched.scheduled_job("cron", hour=12, minute=30)  # 12:30 午報
def phase_noon():    push_to_line(_safe_compose("noon"))

@sched.scheduled_job("cron", hour=18, minute=0)   # 18:00 晚報
def phase_evening(): push_to_line(_safe_compose("evening"))

@sched.scheduled_job("cron", hour=22, minute=30)  # 22:30 夜報
def phase_night():   push_to_line(_safe_compose("night"))

# 每 10 分鐘刷新徽章
@sched.scheduled_job("cron", minute="*/10", second=5)
def badges_refresher():
    try:
        badges_radar.refresh_badges()
    except Exception:
        pass

# 每分鐘：到期提醒 + 清理
@sched.scheduled_job("cron", second=10)
def watch_keeper():
    now = int(time.time())
    ws = list_watches()
    for sym, v in ws.items():
        until = int(v.get("until", 0)); last = int(v.get("last_alert", 0))
        remain = until - now
        if 0 < remain <= 300 and last < (until - 300):
            try:
                push_to_line(f"⏰ {sym} 監控將於 {remain//60} 分後到期（{time.strftime('%H:%M', time.localtime(until))}）")
            except Exception:
                pass
            v["last_alert"] = now
    cleanup_expired(now)

@app.on_event("startup")
def start_sched():
    if not sched.running:
        sched.start()

# ========= 管理/健康檢查 =========
@app.get("/admin/news-score")
def admin_news_score(symbol: str = "BTC"):
    s = news_scoring.get_news_score(symbol.upper())
    return {"symbol": symbol.upper(), "news_score": s}

@app.get("/admin/health")
def admin_health():
    return {"ok": True, "ts": int(time.time())}
