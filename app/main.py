# =========================
# app/main.py 〔覆蓋版・一鍵貼上 R4〕
# Sentinel v8 · FastAPI + APScheduler + LINE Reply
# 內建：/admin/env-lite、/admin/ping-services、/admin/version-snapshot|diff|badge
# 內建：version_diff 後備實作（即使沒有 app/services/version_diff.py 也能跑）
# ＊所有回覆加「【v8R4】」指紋；logs 帶 [WH][v8R4]/[PUSH][v8R4]
# =========================

from __future__ import annotations
import os, re, time, json, hashlib
from zoneinfo import ZoneInfo
from typing import Dict, Any, Tuple
from fastapi import FastAPI, Request
from apscheduler.schedulers.background import BackgroundScheduler

# --- 既有模組 ---
from app.state_store import get_state, save_state, set_watch, cleanup_expired, list_watches
from app.services.prefs import resolve_scheme, set_color_scheme, current_scheme
from app.services import watches as W
from app import trend_integrator, news_scoring
from app import us_stocks, us_news
from app import badges_radar

# ============ version_diff：優先載入正式版，失敗時使用內建後備 ============
BASELINE_PATH = "/tmp/sentinel-v8.version-prev.json"
SCAN_ROOT = "."

def _iter_files(root: str):
    skip_dirs = {".git", "__pycache__", ".venv", "venv", ".render"}
    for dirpath, dirnames, filenames in os.walk(root):
        dn = os.path.basename(dirpath)
        if dn in skip_dirs or "/.venv/" in dirpath or "/venv/" in dirpath:
            continue
        for fn in filenames:
            # 只掃描專案檔（略過超大或 cache 類型）
            if fn.endswith((".py", ".json", ".txt", ".md", ".yaml", ".yml")) or "." in fn:
                p = os.path.join(dirpath, fn)
                # 限制：最多 256 KB 避免 I/O 爆
                try:
                    size = os.path.getsize(p)
                    if size > 262_144:
                        continue
                except Exception:
                    continue
                yield p

def _fingerprint(path: str) -> Tuple[int, int, str]:
    """return (size, mtime, sha1[:8])"""
    try:
        st = os.stat(path)
        size, mtime = int(st.st_size), int(st.st_mtime)
        h = hashlib.sha1()
        with open(path, "rb") as f:
            h.update(f.read())
        return size, mtime, h.hexdigest()[:8]
    except Exception:
        return 0, 0, ""

def _snapshot(root: str) -> Dict[str, Any]:
    items = {}
    root = os.path.abspath(root)
    for p in _iter_files(root):
        rp = os.path.relpath(p, root)
        size, mtime, sh = _fingerprint(p)
        if sh:
            items[rp] = {"size": size, "mtime": mtime, "sha": sh}
    return {"root": root, "ts": int(time.time()), "items": items}

def _diff(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    A, B = a.get("items", {}), b.get("items", {})
    add, delete, modify = [], [], []
    for k in B:
        if k not in A:
            add.append(k)
        else:
            if A[k].get("sha") != B[k].get("sha"):
                modify.append(k)
    for k in A:
        if k not in B:
            delete.append(k)
    return {"add": sorted(add), "delete": sorted(delete), "modify": sorted(modify)}

def _mk_summary(delta: Dict[str, Any], limit: int = 10) -> str:
    a, d, m = len(delta["add"]), len(delta["delete"]), len(delta["modify"])
    lines = [f"📦 版本差異：+{a} −{d} ✎{m}（顯示最多 {limit} 筆）"]
    def cut(lst, mark):
        for i, k in enumerate(lst[:limit], 1):
            lines.append(f"{mark} {i}. {k}")
    cut(delta["add"], "+")
    cut(delta["modify"], "✎")
    cut(delta["delete"], "−")
    return "\n".join(lines)

class _VersionDiffFallback:
    @staticmethod
    def checkpoint_now(root: str = SCAN_ROOT) -> Dict[str, Any]:
        snap = _snapshot(root)
        with open(BASELINE_PATH, "w", encoding="utf-8") as f:
            json.dump(snap, f, ensure_ascii=False)
        return {"ok": True, "count": len(snap["items"])}

    @staticmethod
    def diff_now_vs_prev(root: str = SCAN_ROOT) -> Dict[str, Any]:
        now = _snapshot(root)
        try:
            with open(BASELINE_PATH, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = {"items": {}}
        delta = _diff(prev, now)
        return {"delta": delta, "summary": _mk_summary(delta), "now_count": len(now["items"])}

    @staticmethod
    def get_version_badge() -> Tuple[bool, str]:
        try:
            with open(BASELINE_PATH, "r", encoding="utf-8") as f:
                prev = json.load(f)
        except Exception:
            prev = {"items": {}}
        now = _snapshot(SCAN_ROOT)
        d = _diff(prev, now)
        n = len(d["add"]) + len(d["delete"]) + len(d["modify"])
        return (n > 0, f"版本Δ({n})") if n > 0 else (False, "")

# 嘗試載入正式版；失敗就用 fallback
try:
    from app.services import version_diff as version_diff  # type: ignore
    if version_diff is None:  # 由 __init__.py 給的 None
        raise ImportError("version_diff None")
except Exception:
    version_diff = _VersionDiffFallback()  # type: ignore

# ============ LINE SDK ============
from linebot import LineBotApi
from linebot.models import TextSendMessage

# ========= 基本設定 =========
TZ = ZoneInfo("Asia/Taipei")
app = FastAPI(title="sentinel-v8")

LINE_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_PUSH_TO = os.getenv("LINE_PUSH_TO", "")
line_bot_api = LineBotApi(LINE_ACCESS_TOKEN) if LINE_ACCESS_TOKEN else None

# ========= 啟動流程 =========
@app.on_event("startup")
def on_startup():
    print("[BOOT][v8R4] starting…")
    _ = get_state(); save_state()
    try:
        badges_radar.refresh_badges()
        print("[BOOT][v8R4] badges refreshed")
    except Exception as e:
        print("[BOOT][v8R4] badges init err:", e)
    # 初始化版本基準（若不存在）
    try:
        if not os.path.exists(BASELINE_PATH):
            version_diff.checkpoint_now(".")
            print("[BOOT][v8R4] version baseline created")
    except Exception as e:
        print("[BOOT][v8R4] version baseline err:", e)

# ========= 健康檢查 / 診斷 =========
@app.get("/")
def root():
    return {"ok": True, "tag": "v8R4", "ts": int(time.time())}

@app.get("/admin/env-lite")
def env_lite():
    return {
        "tag": "v8R4",
        "has_line_token": bool(LINE_ACCESS_TOKEN),
        "has_push_target": bool(LINE_PUSH_TO),
    }

@app.get("/admin/ping-services")
def ping_services():
    def check(modname):
        try:
            __import__(modname)
            return True, ""
        except Exception as e:
            return False, str(e)
    ok_prefs, err_prefs = check("app.services.prefs")
    ok_watches, err_watches = check("app.services.watches")
    # version_diff 可選
    try:
        from app.services import version_diff as _vd  # type: ignore
        ok_vd = _vd is not None
        err_vd = "" if ok_vd else "module not present (optional)"
    except Exception as e:
        ok_vd, err_vd = False, str(e)
    return {"ok": {"prefs": ok_prefs, "watches": ok_watches, "version_diff": ok_vd},
            "errors": {"prefs": err_prefs, "watches": err_watches, "version_diff": err_vd}}

# ========= 版本 API（內建）=========
@app.post("/admin/version-snapshot")
def admin_version_snapshot():
    try:
        res = version_diff.checkpoint_now(".")
        return {"ok": True, "res": res}
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/admin/version-diff")
def admin_version_diff(save: int = 0):
    try:
        res = version_diff.diff_now_vs_prev(".")
        if save:
            version_diff.checkpoint_now(".")
            res["saved"] = True
        return res
    except Exception as e:
        return {"ok": False, "error": str(e)}

@app.get("/admin/version-badge")
def admin_version_badge():
    try:
        has, badge = version_diff.get_version_badge()
        return {"has_delta": has, "badge": badge}
    except Exception as e:
        return {"ok": False, "error": str(e)}

# ========= LINE 推播（排程用）=========
def push_to_line(text: str):
    msg = f"【v8R4】{text}"
    if line_bot_api and LINE_PUSH_TO:
        try:
            line_bot_api.push_message(LINE_PUSH_TO, TextSendMessage(msg))
            print("[PUSH][v8R4] sent to LINE_PUSH_TO")
            return
        except Exception as e:
            print(f"[PUSH][v8R4] error:", e)
    print("[PUSH][v8R4] console:", msg)

# ========= LINE Webhook（回覆＋強 log）=========
@app.post("/line/webhook")
async def line_webhook(request: Request):
    payload = await request.json()
    print("[WH][v8R4] inbound:", json.dumps(payload, ensure_ascii=False)[:400])
    events = payload.get("events", [])
    out = []

    for ev in events:
        raw = (ev.get("message", {}) or {}).get("text", "") or ""
        reply_token = ev.get("replyToken")
        t = re.sub(r"\s+", " ", raw.replace("\u3000", " ")).strip()
        print(f"[WH][v8R4] text='{t}' reply_token={'Y' if reply_token else 'N'}")

        def reply(msg: str):
            tagged = f"【v8R4】{msg}"
            out.append(tagged)
            if line_bot_api and reply_token:
                try:
                    line_bot_api.reply_message(reply_token, TextSendMessage(tagged))
                    print("[WH][v8R4] replied via Reply API")
                except Exception as e:
                    print("[WH][v8R4] reply error:", e)

        # 版本核對 / 差異（最優先，含別名）
        if t in ("版本核對", "版本差異", "版本差异", "version diff", "version-diff", "ver diff"):
            try:
                diff = version_diff.diff_now_vs_prev(".")
                reply(diff.get("summary") or "版本比對完成（無摘要）")
            except Exception as e:
                reply(f"版本比對失敗：{e}")
            continue

        # 配色
        if t.startswith("顏色"):
            scheme = resolve_scheme(t)
            reply(set_color_scheme(scheme) if scheme else "請說明要切換到「台股」或「美股」配色。")
            continue

        # 新聞 <幣>
        m_news = re.match(r"^\s*新聞\s+([A-Za-z0-9_\-\.]+)\s*$", t)
        if m_news:
            sym = m_news.group(1).upper()
            heads = news_scoring.recent_headlines(sym, k=5)
            if not heads:
                reply(f"{sym} 近 24 小時無新聞或暫時無法取得。")
            else:
                lines = [f"🗞️ {sym} 近 24 小時重點新聞（中文）"]
                for i, h in enumerate(heads, 1):
                    lines.append(f"{i}. {h['title_zh']} 〔{h['timeago']}〕")
                reply("\n".join(lines))
            continue

        # 美股
        if t == "美股":
            block = us_stocks.format_us_full()
            nblk = us_news.format_us_news_block(k_each=2, max_topics=6)
            reply(f"{block}\n\n{nblk}")
            continue

        # 監控延長 + / 停止 -
        sym = W.parse_plus(t)
        if sym:
            reply(W.extend(sym, hours=1)); continue
        sym = W.parse_minus(t)
        if sym:
            reply(W.stop(sym)); continue

        # 總覽
        if t in ("總覽", "監控", "監控列表", "監控清單"):
            reply(W.summarize()); continue

        # 今日強勢 / 今日弱勢（含中文新聞）
        if t in ("今日強勢", "今日弱勢"):
            scheme = current_scheme()
            want_strong = (t == "今日強勢")
            try:
                msg = trend_integrator.generate_side(single=t, scheme=scheme, want_strong=want_strong, topn=3)
                syms = []
                for line in msg.splitlines():
                    m = re.search(r"\b([A-Z]{2,10})\b", line)
                    if m:
                        s = m.group(1)
                        if s not in ("S", "N", "T"): syms.append(s)
                syms = [s for s in syms if s.isalpha()]
                hmap = news_scoring.batch_recent_headlines(syms, k=2) if syms else {}
                if hmap:
                    msg += "\n\n🗞️ 中文新聞精選"
                    for s in syms:
                        heads = hmap.get(s) or []
                        if heads:
                            msg += f"\n• {s}"
                            for h in heads:
                                msg += f"\n  - {h['title_zh']} 〔{h['timeago']}〕"
            except Exception as e:
                msg = f"{t} 生成失敗：{e}\n（稍後重試或檢查外網）"
            reply(msg); continue

        # <幣> 做多/做空：建立 1h 監控
        m = re.match(r"^\s*([A-Za-z0-9_\-\.]+)\s*(做多|做空)\s*$", t)
        if m:
            sym, action = m.group(1).upper(), m.group(2)
            set_watch(sym, int(time.time()) + 3600)
            reply(f"{sym} 設定為{action}，並已監控 1 小時。")
            continue

        # 預設回覆
        reply("指令：今日強勢｜今日弱勢｜美股｜新聞 <幣>｜顏色 台股/美股｜總覽｜版本核對｜版本差異")

    return {"messages": out}

# ========= 報表（四時段）=========
def compose_report(phase: str) -> str:
    scheme = current_scheme()

    badges = []
    try: badges = badges_radar.get_badges()
    except Exception: badges = []

    try:
        has_delta, badge_txt = version_diff.get_version_badge()
        if has_delta and badge_txt not in badges: badges.append(badge_txt)
    except Exception:
        pass

    badge_str = (" ｜ " + " ".join(f"[{b}]" for b in badges)) if badges else ""

    try:
        ti = trend_integrator.generate_report(scheme=scheme, topn=3)
    except Exception as e:
        ti = f"主升浪清單生成失敗：{e}"

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
    if us_block: parts += [us_block, ""]
    parts.append(ti)
    return "\n".join(parts)

# ========= 排程 =========
sched = BackgroundScheduler(timezone=str(TZ))

def _safe_compose(phase: str) -> str:
    try: return compose_report(phase)
    except Exception as e: return f"【{phase}報】生成失敗：{e}"

@sched.scheduled_job("cron", hour=9, minute=30)
def phase_morning(): push_to_line(_safe_compose("morning"))

@sched.scheduled_job("cron", hour=12, minute=30)
def phase_noon():    push_to_line(_safe_compose("noon"))

@sched.scheduled_job("cron", hour=18, minute=0)
def phase_evening(): push_to_line(_safe_compose("evening"))

@sched.scheduled_job("cron", hour=22, minute=30)
def phase_night():   push_to_line(_safe_compose("night"))

@sched.scheduled_job("cron", minute="*/10", second=5)
def badges_refresher():
    try: badges_radar.refresh_badges()
    except Exception: pass

@sched.scheduled_job("cron", second=10)
def watch_keeper():
    now = int(time.time())
    ws = list_watches()
    for sym, v in ws.items():
        until = int(v.get("until", 0)); last = int(v.get("last_alert", 0))
        remain = until - now
        if 0 < remain <= 300 and last < (until - 300):
            try: push_to_line(f"⏰ {sym} 監控將於 {remain//60} 分後到期（{time.strftime('%H:%M', time.localtime(until))}）")
            except Exception: pass
            v["last_alert"] = now
    cleanup_expired(now)

@app.on_event("startup")
def start_sched():
    if not sched.running: sched.start()

@app.get("/admin/news-score")
def admin_news_score(symbol: str = "BTC"):
    s = news_scoring.get_news_score(symbol.upper())
    return {"symbol": symbol.upper(), "news_score": s}

@app.get("/admin/health")
def admin_health():
    return {"ok": True, "tag": "v8R4", "ts": int(time.time())}
