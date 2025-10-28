from __future__ import annotations
from fastapi import FastAPI, Request
from apscheduler.schedulers.background import BackgroundScheduler
from zoneinfo import ZoneInfo
import re, time

from app.state_store import get_state, save_state, set_watch, cleanup_expired, list_watches
from app.services.prefs import resolve_scheme, set_color_scheme, current_scheme
from app.services import watches as W
from app import trend_integrator, news_scoring

def push_to_line(text: str):
    print("[LINE]", text)

TZ = ZoneInfo("Asia/Taipei")
app = FastAPI(title="sentinel-v8")

@app.on_event("startup")
def on_startup():
    _ = get_state()
    save_state()

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

        # 新聞 <幣>
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

        # + 延長、- 停止
        sym = W.parse_plus(t)
        if sym:
            replies.append(W.extend(sym, hours=1)); continue
        sym = W.parse_minus(t)
        if sym:
            replies.append(W.stop(sym)); continue

        # 總覽／監控查詢
        if t in ("總覽", "監控", "監控列表", "監控清單"):
            replies.append(W.summarize()); continue

        # 今日強勢／今日弱勢
        if t in ("今日強勢", "今日弱勢"):
            scheme = current_scheme()
            want_strong = (t == "今日強勢")
            try:
                # 用 trend_integrator 產生清單（含分數）
                msg = trend_integrator.generate_side(single=t, scheme=scheme, want_strong=want_strong, topn=3)
                # 在清單末尾附上每個幣的中文新聞（各 2 則）
                # 先找出該頁面列到的 symbol
                syms = []
                for line in msg.splitlines():
                    m = re.search(r"\b([A-Z]{2,10})\b", line)
                    if m:
                        s = m.group(1)
                        if s not in ("S", "N", "T"):  # 避免誤抓分數標記
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
                replies.append(msg)
            except Exception as e:
                replies.append(f"{t} 生成失敗：{e}\n（稍後重試或檢查外網）")
            continue

        # <幣> 做多／做空（示意：建立 1h 監控）
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
    # 主升浪排行榜
    try:
        ti = trend_integrator.generate_report(scheme=scheme, topn=3)
    except Exception as e:
        ti = f"主升浪清單生成失敗：{e}"
    # 從文字抓出該次上榜的 symbols（最多 6：3 多 + 3 空）
    syms = []
    for line in ti.splitlines():
        m = re.search(r"\b([A-Z]{2,10})\b", line)
        if m:
            s = m.group(1)
            if s not in ("S", "N", "T"):
                syms.append(s)
    syms = [s for s in syms if s.isalpha()]
    # 中文新聞：每個幣 1~2 則
    news_block = ""
    if syms:
        try:
            hmap = news_scoring.batch_recent_headlines(syms, k=2)
            if hmap:
                lines = ["🗞️ 中文新聞精選"]
                for s in syms:
                    heads = hmap.get(s) or []
                    if not heads: 
                        continue
                    lines.append(f"• {s}")
                    for h in heads:
                        lines.append(f"  - {h['title_zh']} 〔{h['timeago']}〕")
                news_block = "\n" + "\n".join(lines)
        except Exception:
            news_block = ""
    watches_snapshot = W.summarize()
    return f"【{phase}報】配色：{scheme}\n監控：{watches_snapshot}\n\n{ti}{news_block}"

@sched.scheduled_job("cron", hour=9, minute=30)  # 09:30
def phase_morning(): push_to_line(compose_report("morning"))

@sched.scheduled_job("cron", hour=12, minute=30) # 12:30
def phase_noon():    push_to_line(compose_report("noon"))

@sched.scheduled_job("cron", hour=18, minute=0)  # 18:00
def phase_evening(): push_to_line(compose_report("evening"))

@sched.scheduled_job("cron", hour=22, minute=30) # 22:30
def phase_night():   push_to_line(compose_report("night"))

@sched.scheduled_job("cron", second=10)  # 每分鐘提醒/清理
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
