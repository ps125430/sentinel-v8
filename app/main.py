from __future__ import annotations
from fastapi import FastAPI, Request
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo
import re, time

from app.state_store import get_state, save_state, set_watch, cleanup_expired, list_watches
from app.services.prefs import resolve_scheme, set_color_scheme, current_scheme
from app.services import watches as W
from app import trend_integrator, news_scoring
from app import us_stocks, us_news
from app import badges_radar  # ← 新增：徽章引擎

# ====== 這裡替換成你的 LINE 推播實作 ======
def push_to_line(text: str):
    print("[LINE]", text)

TZ = ZoneInfo("Asia/Taipei")
app = FastAPI(title="sentinel-v8")

# === Sentinel v8 :: Admin Routers ===
from app import admin_version
app.include_router(admin_version.router)


# ====== 啟動：確保狀態檔存在 ======
@app.on_event("startup")
def on_startup():
    _ = get_state()
    save_state()
    # 啟動時先刷新一次徽章，避免空值
    try:
        badges_radar.refresh_badges()
    except Exception:
        pass

# ====== LINE Webhook（文字指令處理）======
@app.post("/line/webhook")
async def line_webhook(request: Request):
    payload = await request.json()
    events = payload.get("events", [])
    replies = []

    for ev in events:
        text = (ev.get("message", {}) or {}).get("text", "") or ""
        t = re.sub(r"\s+", " ", text.replace("\u3000", " ")).strip()

        # --- 配色 ---
        if t.startswith("顏色"):
            scheme = resolve_scheme(t)
            replies.append(set_color_scheme(scheme) if scheme else "請說明要切換到「台股」或「美股」配色。")
            continue

        # --- 新聞 <幣> ---
        m_news = re.match(r"^\s*新聞\s+([A-Za-z0-9_\-\.]+)\s*$", t)
        if m_news:
            sym = m_news.group(1).upper()
            heads = news_scoring.recent_headlines(sym, k=5)
            if not heads:
                replies.append(f"{sym} 近 24 小時無新聞或暫時無法取得。")
            else:
                lines = [f"🗞️ {sym} 近 24 小時重點新聞（中文）"]
                for i, h in enumerate(heads, 1):
                    lines.append(f"{i}. {h['title_zh']} 〔{h['timeago']}〕")
                replies.append("\n".join(lines))
            continue

        # --- 美股（詳細版：逐檔一行 + 中文新聞重點） ---
        if t == "美股":
            block = us_stocks.format_us_full()
            nblk = us_news.format_us_news_block(k_each=2, max_topics=6)
            replies.append(f"{block}\n\n{nblk}")
            continue

        # --- 監控延長 +/停止 - ---
        sym = W.parse_plus(t)
        if sym:
            replies.append(W.extend(sym, hours=1)); continue
        sym = W.parse_minus(t)
        if sym:
            replies.append(W.stop(sym)); continue

        # --- 總覽 ---
        if t in ("總覽", "監控", "監控列表", "監控清單"):
            replies.append(W.summarize()); continue

        # --- 今日強勢 / 今日弱勢（含中文新聞精選） ---
        if t in ("今日強勢", "今日弱勢"):
            scheme = current_scheme()
            want_strong = (t == "今日強勢")
            try:
                msg = trend_integrator.generate_side(single=t, scheme=scheme, want_strong=want_strong, topn=3)
                # 提取此次上榜 symbols
                syms = []
                for line in msg.splitlines():
                    m = re.search(r"\b([A-Z]{2,10})\b", line)
                    if m:
                        s = m.group(1)
                        if s not in ("S", "N", "T"):
                            syms.append(s)
                syms = [s for s in syms if s.isalpha()]
                # 附加各幣 2 則中文新聞
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
                replies.append(msg)
            except Exception as e:
                replies.append(f"{t} 生成失敗：{e}\n（稍後重試或檢查外網）")
            continue

        # --- <幣> 做多/做空：建立 1h 監控 ---
        m = re.match(r"^\s*([A-Za-z0-9_\-\.]+)\s*(做多|做空)\s*$", t)
        if m:
            sym, action = m.group(1).upper(), m.group(2)
            set_watch(sym, int(time.time()) + 3600)
            replies.append(f"{sym} 設定為{action}，並已監控 1 小時。")
            continue

    return {"messages": replies}

# ====== 報表組裝（含徽章 + 美股三行分組）======
def compose_report(phase: str) -> str:
    scheme = current_scheme()

    # 標題行附徽章
    badges = []
    try:
        badges = badges_radar.get_badges()
    except Exception:
        badges = []
    badge_str = (" ｜ " + " ".join(f"[{b}]" for b in badges)) if badges else ""

    # Crypto 主升浪排行榜
    try:
        ti = trend_integrator.generate_report(scheme=scheme, topn=3)
    except Exception as e:
        ti = f"主升浪清單生成失敗：{e}"

    # 美股區塊：夜報=開盤雷達+美股新聞；早報=隔夜回顧；其他時段略過
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
        parts.append(us_block); parts.append("")
    parts.append(ti)
    return "\n".join(parts)

# ====== 排程（四時段推播 + 徽章更新 + 每分鐘提醒/清理）======
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

# 每 10 分鐘刷新徽章（背景雷達）
@sched.scheduled_job("cron", minute="*/10", second=5)
def badges_refresher():
    try:
        badges_radar.refresh_badges()
    except Exception:
        pass

# 每分鐘：到期提醒 + 清理過期監控
@sched.scheduled_job("cron", second=10)
def watch_keeper():
    now = int(time.time())
    ws = list_watches()
    for sym, v in ws.items():
        until = int(v.get("until", 0)); last = int(v.get("last_alert", 0))
        remain = until - now
        if 0 < remain <= 300 and last < (until - 300):
            push_to_line(f"⏰ {sym} 監控將於 {remain//60} 分後到期（{time.strftime('%H:%M', time.localtime(until))}）")
            v["last_alert"] = now
    cleanup_expired(now)

@app.on_event("startup")
def start_sched():
    if not sched.running:
        sched.start()

# ====== 管理/健康檢查 ======
@app.get("/admin/news-score")
def admin_news_score(symbol: str = "BTC"):
    s = news_scoring.get_news_score(symbol.upper())
    return {"symbol": symbol.upper(), "news_score": s}

@app.get("/admin/health")
def admin_health():
    return {"ok": True, "ts": int(time.time())}
