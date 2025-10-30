# =========================
# app/main.py 〔覆蓋版・v8R7｜顯示價格可開關〕
# 新增：LINE 指令「顯示價格 開啟｜關閉」
# 台股/美股報表與詳細指令依 prefs.show_price 顯示價格
# 幣圈：在「今日強勢/今日弱勢」中（若開啟）盡力附上 $價格（抓不到則略過）
# ＊所有回覆帶【v8R7】
# =========================

from __future__ import annotations
import os, re, time, json, hashlib, inspect
from zoneinfo import ZoneInfo
from typing import Dict, Any, Tuple, List
from fastapi import FastAPI, Request, HTTPException
from apscheduler.schedulers.background import BackgroundScheduler

from app.state_store import get_state, save_state, set_watch, cleanup_expired, list_watches
from app.services.prefs import resolve_scheme, set_color_scheme, current_scheme
from app.services import watches as W
from app import trend_integrator, news_scoring
from app import us_stocks, us_news
from app import badges_radar
from app import tw_stocks  # 台股
# 可選：若有台股新聞模組就載入；沒有也不影響
try:
    from app import tw_news
except Exception:
    tw_news = None  # type: ignore

# ======= save_state 相容層（無論舊版/新版簽名都可用）=======
import inspect as _ins
try:
    _SAVE_WANTS_ARG = len(_ins.signature(save_state).parameters) >= 1
except Exception:
    _SAVE_WANTS_ARG = False

def _persist(st=None):
    try:
        if _SAVE_WANTS_ARG and st is not None:
            save_state(st)  # type: ignore
        else:
            save_state()
    except TypeError:
        try: save_state()
        except Exception as e: print("[STATE] save_state failed:", e)
    except Exception as e:
        print("[STATE] save_state failed:", e)

# ======= 版本差異 fallback =======
BASELINE_PATH = "/tmp/sentinel-v8.version-prev.json"
SCAN_ROOT = "."

def _iter_files(root: str):
    skip_dirs = {".git", "__pycache__", ".venv", "venv", ".render"}
    for dirpath, dirnames, filenames in os.walk(root):
        dn = os.path.basename(dirpath)
        if dn in skip_dirs or "/.venv/" in dirpath or "/venv/" in dirpath:
            continue
        for fn in filenames:
            if fn.endswith((".py", ".json", ".txt", ".md", ".yaml", ".yml")) or "." in fn:
                p = os.path.join(dirpath, fn)
                try:
                    size = os.path.getsize(p)
                    if size > 262_144: continue
                except Exception:
                    continue
                yield p

import hashlib as _hl
def _fingerprint(path: str) -> Tuple[int, int, str]:
    try:
        st = os.stat(path)
        size, mtime = int(st.st_size), int(st.st_mtime)
        h = _hl.sha1()
        with open(path, "rb") as f: h.update(f.read())
        return size, mtime, h.hexdigest()[:8]
    except Exception:
        return 0, 0, ""

def _snapshot(root: str) -> Dict[str, Any]:
    items = {}
    root = os.path.abspath(root)
    for p in _iter_files(root):
        rp = os.path.relpath(p, root)
        size, mtime, sh = _fingerprint(p)
        if sh: items[rp] = {"size": size, "mtime": mtime, "sha": sh}
    return {"root": root, "ts": int(time.time()), "items": items}

def _diff(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    A, B = a.get("items", {}), b.get("items", {})
    add, delete, modify = [], [], []
    for k in B:
        if k not in A: add.append(k)
        else:
            if A[k].get("sha") != B[k].get("sha"): modify.append(k)
    for k in A:
        if k not in B: delete.append(k)
    return {"add": sorted(add), "delete": sorted(delete), "modify": sorted(modify)}

def _mk_summary(delta: Dict[str, Any], limit: int = 10) -> str:
    a, d, m = len(delta["add"]), len(delta["delete"]), len(delta["modify"])
    lines = [f"📦 版本差異：+{a} −{d} ✎{m}（顯示最多 {limit} 筆）"]
    def cut(lst, mark):
        for i, k in enumerate(lst[:limit], 1):
            lines.append(f"{mark} {i}. {k}")
    cut(delta["add"], "+"); cut(delta["modify"], "✎"); cut(delta["delete"], "−")
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

try:
    from app.services import version_diff as version_diff  # type: ignore
    if version_diff is None: raise ImportError("version_diff None")
except Exception:
    version_diff = _VersionDiffFallback()  # type: ignore

# ============ LINE ============
from linebot import LineBotApi
from linebot.models import TextSendMessage

TZ = ZoneInfo("Asia/Taipei")
app = FastAPI(title="sentinel-v8")

LINE_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN", "")
LINE_PUSH_TO = os.getenv("LINE_PUSH_TO", "")
line_bot_api = LineBotApi(LINE_ACCESS_TOKEN) if LINE_ACCESS_TOKEN else None

# ===== 喚醒與觸發安全：Token =====
WAKER_TOKEN = os.getenv("WAKER_TOKEN", "")

def _chk_token(token: str):
    if not WAKER_TOKEN or token != WAKER_TOKEN:
        raise HTTPException(status_code=401, detail="bad token")

# ========= 偏好開關（含顯示價格） =========
def ensure_prefs_defaults():
    st = get_state()
    prefs = st.setdefault("prefs", {})
    prefs.setdefault("enable_us", True)
    prefs.setdefault("enable_crypto", True)
    prefs.setdefault("enable_tw", True)
    prefs.setdefault("show_price", True)  # ★ 新增：顯示價格預設開啟
    st.setdefault("manual_push_ts", {})
    _persist(st)
    return prefs

# ========= 推播封裝 =========
def push_to_line(text: str):
    msg = f"【v8R7】{text}"
    if line_bot_api and LINE_PUSH_TO:
        try:
            line_bot_api.push_message(LINE_PUSH_TO, TextSendMessage(msg))
            print("[PUSH][v8R7] sent to LINE_PUSH_TO"); return
        except Exception as e:
            print(f"[PUSH][v8R7] error:", e)
    print("[PUSH][v8R7] console:", msg)

# ========= 啟動 =========
@app.on_event("startup")
def on_startup():
    print("[BOOT][v8R7] starting…")
    _ = get_state(); _persist()
    ensure_prefs_defaults()
    try:
        badges_radar.refresh_badges()
        print("[BOOT][v8R7] badges refreshed")
    except Exception as e:
        print("[BOOT][v8R7] badges init err:", e)
    try:
        if not os.path.exists(BASELINE_PATH):
            version_diff.checkpoint_now(".")
            print("[BOOT][v8R7] version baseline created")
    except Exception as e:
        print("[BOOT][v8R7] version baseline err:", e)

# ========= 管理/診斷 =========
@app.get("/")
def root():
    return {"ok": True, "tag": "v8R7", "ts": int(time.time())}

@app.get("/admin/env-lite")
def env_lite():
    p = get_state().get("prefs", {})
    return {
        "tag": "v8R7",
        "has_line_token": bool(LINE_ACCESS_TOKEN),
        "has_push_target": bool(LINE_PUSH_TO),
        "prefs": p
    }

@app.get("/admin/warm")
def admin_warm(token: str = ""):
    _chk_token(token)
    _ = get_state()
    try: badges_radar.refresh_badges()
    except Exception: pass
    return {"ok": True, "tag": "v8R7", "warmed": True, "ts": int(time.time())}

@app.post("/admin/trigger-report")
@app.get("/admin/trigger-report")
def trigger_report(phase: str, token: str = ""):
    _chk_token(token)
    if phase not in ("morning","noon","evening","night"):
        raise HTTPException(400, "bad phase")
    msg = compose_report(phase)
    push_to_line(f"🪄 手動觸發 {phase}報\n{msg}")
    st = get_state(); st.setdefault("manual_push_ts", {})[phase] = int(time.time()); _persist(st)
    return {"ok": True, "pushed": True, "phase": phase}

@app.post("/admin/version-snapshot")
def admin_version_snapshot():
    try: return {"ok": True, "res": version_diff.checkpoint_now(".")}
    except Exception as e: return {"ok": False, "error": str(e)}

@app.get("/admin/version-diff")
def admin_version_diff(save: int = 0):
    try:
        res = version_diff.diff_now_vs_prev(".")
        if save: version_diff.checkpoint_now("."); res["saved"] = True
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

# ========= 幣價輔助（顯示價格時用；失敗就略過）=========
# 常用幣種對應 CoinGecko id
_CG = {
    "BTC":"bitcoin","ETH":"ethereum","SOL":"solana","BNB":"binancecoin",
    "AVAX":"avalanche-2","LINK":"chainlink","ADA":"cardano","XRP":"ripple",
    "DOGE":"dogecoin","TON":"the-open-network","DOT":"polkadot"
}

import requests
def _get_prices_usd(symbols: List[str]) -> Dict[str, float]:
    ids = [ _CG[s] for s in symbols if s in _CG ]
    if not ids: return {}
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": ",".join(ids), "vs_currencies": "usd"},
            timeout=6,
        )
        r.raise_for_status()
        data = r.json()
        out = {}
        inv = {v:k for k,v in _CG.items()}
        for cg_id, obj in data.items():
            sym = inv.get(cg_id)
            if sym and "usd" in obj:
                out[sym] = float(obj["usd"])
        return out
    except Exception:
        return {}

# ========= LINE Webhook =========
@app.post("/line/webhook")
async def line_webhook(request: Request):
    payload = await request.json()
    print("[WH][v8R7] inbound:", json.dumps(payload, ensure_ascii=False)[:400])
    events = payload.get("events", [])
    out = []

    for ev in events:
        raw = (ev.get("message", {}) or {}).get("text", "") or ""
        reply_token = ev.get("replyToken")
        t = re.sub(r"\s+", " ", raw.replace("\u3000", " ")).strip()
        print(f"[WH][v8R7] text='{t}' reply_token={'Y' if reply_token else 'N'}")

        def reply(msg: str):
            tagged = f"【v8R7】{msg}"
            out.append(tagged)
            if line_bot_api and reply_token:
                try:
                    line_bot_api.reply_message(reply_token, TextSendMessage(tagged))
                    print("[WH][v8R7] replied via Reply API")
                except Exception as e:
                    print("[WH][v8R7] reply error:", e)

        # === 模組開關（美股 / 台股 / 虛擬貨幣）===
        m_toggle = re.match(r"^(美股|台股|虛擬貨幣)\s*(開啟|關閉)$", t)
        if m_toggle:
            mod, act = m_toggle.groups()
            key = {"美股":"enable_us","台股":"enable_tw","虛擬貨幣":"enable_crypto"}[mod]
            val = (act == "開啟")
            st = get_state(); st.setdefault("prefs", {})[key] = val; _persist(st)
            prefs = st["prefs"]
            reply(f"{mod} 已{act}。目前：美股={'開' if prefs.get('enable_us') else '關'}｜台股={'開' if prefs.get('enable_tw') else '關'}｜幣圈={'開' if prefs.get('enable_crypto') else '關'}")
            continue

        # === 顯示價格 開啟/關閉 ===
        m_price = re.match(r"^顯示價格\s*(開啟|關閉)$", t)
        if m_price:
            on = (m_price.group(1) == "開啟")
            st = get_state(); st.setdefault("prefs", {})["show_price"] = on; _persist(st)
            reply(f"顯示價格已{'開啟' if on else '關閉'}。")
            continue

        if t in ("模組狀態", "狀態", "status"):
            prefs = get_state().get("prefs", {})
            reply(f"模組狀態：美股={'開' if prefs.get('enable_us', True) else '關'}｜台股={'開' if prefs.get('enable_tw', True) else '關'}｜幣圈={'開' if prefs.get('enable_crypto', True) else '關'}｜顯示價格={'開' if prefs.get('show_price', True) else '關'}")
            continue

        # 版本核對/差異
        if t in ("版本核對","版本差異","版本差异","version diff","version-diff","ver diff"):
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

        # === 手動重發報表：早報 / 午報 / 晚報 / 夜報 ===
        if t in ("早報","午報","晚報","夜報"):
            phase_map = {"早報":"morning","午報":"noon","晚報":"evening","夜報":"night"}
            ph = phase_map[t]
            msg = compose_report(ph)
            push_to_line(f"🪄 手動重發 {t}\n{msg}")
            reply(f"{t}已重發（若訊息較長，請看最新推送）")
            continue

        # 新聞 <幣>
        m_news = re.match(r"^\s*新聞\s+([A-Za-z0-9_\-\.]+)\s*$", t)
        if m_news:
            sym = m_news.group(1).upper()
            heads = news_scoring.recent_headlines(sym, k=5)
            if not heads: reply(f"{sym} 近 24 小時無新聞或暫時無法取得。")
            else:
                lines = [f"🗞️ {sym} 近 24 小時重點新聞（中文）"]
                for i, h in enumerate(heads, 1):
                    lines.append(f"{i}. {h['title_zh']} 〔{h['timeago']}〕")
                reply("\n".join(lines))
            continue

        # 美股詳細
        if t == "美股":
            prefs = get_state().get("prefs", {})
            if not prefs.get("enable_us", True):
                reply("美股模組目前關閉。可用：『美股 開啟』"); continue
            block = us_stocks.format_us_full(show_price=prefs.get("show_price", True))
            nblk = us_news.format_us_news_block(k_each=2, max_topics=6)
            reply(f"{block}\n\n{nblk}"); continue

        # 台股詳細
        if t == "台股":
            prefs = get_state().get("prefs", {})
            if not prefs.get("enable_tw", True):
                reply("台股模組目前關閉。可用：『台股 開啟』"); continue
            reply(tw_stocks.format_tw_full(show_price=prefs.get("show_price", True))); continue

        # 監控延長 + / 停止 -
        sym = W.parse_plus(t)
        if sym: reply(W.extend(sym, hours=1)); continue
        sym = W.parse_minus(t)
        if sym: reply(W.stop(sym)); continue

        # 總覽
        if t in ("總覽","監控","監控列表","監控清單"):
            reply(W.summarize()); continue

        # 今日強勢/弱勢（幣圈；若開啟顯示價格則嘗試附上）
        if t in ("今日強勢", "今日弱勢"):
            prefs = get_state().get("prefs", {})
            if not prefs.get("enable_crypto", True):
                reply("虛擬貨幣模組目前關閉。可用：『虛擬貨幣 開啟』"); continue
            scheme = current_scheme(); want_strong = (t == "今日強勢")
            try:
                msg = trend_integrator.generate_side(single=t, scheme=scheme, want_strong=want_strong, topn=3)
                # 如果要顯示價格→嘗試抓取
                if prefs.get("show_price", True):
                    syms: List[str] = []
                    for line in msg.splitlines():
                        m = re.search(r"\b([A-Z]{2,10})\b", line)
                        if m:
                            s = m.group(1)
                            if s not in ("S","N","T") and s.isalpha(): syms.append(s)
                    syms = sorted(set(syms))
                    prices = _get_prices_usd(syms) if syms else {}
                    if prices:
                        # 在行尾附上價格
                        new_lines = []
                        for line in msg.splitlines():
                            m = re.search(r"\b([A-Z]{2,10})\b", line)
                            if m:
                                s = m.group(1)
                                p = prices.get(s)
                                if p:
                                    line = f"{line}（${p:,.0f}）"
                            new_lines.append(line)
                        msg = "\n".join(new_lines)
                # 附加各幣 2 則中文新聞
                syms2: List[str] = []
                for line in msg.splitlines():
                    m = re.search(r"\b([A-Z]{2,10})\b", line)
                    if m:
                        s = m.group(1)
                        if s not in ("S","N","T") and s.isalpha(): syms2.append(s)
                hmap = news_scoring.batch_recent_headlines(syms2, k=2) if syms2 else {}
                if hmap:
                    msg += "\n\n🗞️ 中文新聞精選"
                    for s in syms2:
                        heads = hmap.get(s) or []
                        if heads:
                            msg += f"\n• {s}"
                            for h in heads:
                                msg += f"\n  - {h['title_zh']} 〔{h['timeago']}〕"
            except Exception as e:
                msg = f"{t} 生成失敗：{e}\n（外部資料源可能限流 429/451，稍後再試）"
            reply(msg); continue

        # 幣 做多/做空
        m = re.match(r"^\s*([A-Za-z0-9_\-\.]+)\s*(做多|做空)\s*$", t)
        if m:
            sym, action = m.group(1).upper(), m.group(2)
            set_watch(sym, int(time.time()) + 3600)
            reply(f"{sym} 設定為{action}，並已監控 1 小時。"); continue

        # 預設回覆
        reply("指令：早報｜午報｜晚報｜夜報｜台股｜美股｜今日強勢｜今日弱勢｜新聞 <幣>｜顯示價格 開啟/關閉｜顏色 台股/美股｜總覽｜版本核對｜版本差異｜模組狀態｜（美股/台股/虛擬貨幣）開啟/關閉")

    return {"messages": out}

# ========= 報表（四時段；台股三行分組＋早/午可附台股新聞）=========
def compose_report(phase: str) -> str:
    prefs = ensure_prefs_defaults()
    scheme = current_scheme()
    show_price = prefs.get("show_price", True)

    # 徽章
    badges = []
    try: badges = badges_radar.get_badges()
    except Exception: badges = []
    try:
        has_delta, badge_txt = version_diff.get_version_badge()
        if has_delta and badge_txt not in badges: badges.append(badge_txt)
    except Exception: pass
    badge_str = (" ｜ " + " ".join(f"[{b}]" for b in badges)) if badges else ""

    parts = [f"【{phase}報】配色：{scheme}{badge_str}", f"監控：{W.summarize()}", ""]

    # 台股：早/午/晚顯示三行分組；且早/午附台股新聞
    if prefs.get("enable_tw", True) and phase in ("morning","noon","evening"):
        try:
            parts += [tw_stocks.format_tw_block(phase=phase, show_price=show_price), ""]
        except Exception as e:
            parts += [f"台股區塊生成失敗：{e}", ""]
        if phase in ("morning","noon") and tw_news:
            try:
                parts += [tw_news.format_tw_news_block(k=3), ""]
            except Exception as e:
                parts += [f"台股新聞取得失敗：{e}", ""]

    # 美股：夜＝開盤雷達＋新聞；早＝隔夜回顧
    if prefs.get("enable_us", True):
        if phase == "night":
            try:
                us_block = us_stocks.format_us_block(phase="night", show_price=show_price)
                us_news_block = us_news.format_us_news_block(k_each=2, max_topics=6)
                parts += [f"{us_block}\n\n{us_news_block}", ""]
            except Exception as e:
                parts += [f"美股區塊生成失敗：{e}", ""]
        elif phase == "morning":
            try:
                parts += [us_stocks.format_us_block(phase="morning", show_price=show_price), ""]
            except Exception as e:
                parts += [f"美股區塊生成失敗：{e}", ""]

    # 幣圈主升浪（排程報表維持趨勢重點，不強制附價）
    if prefs.get("enable_crypto", True):
        try:
            parts.append(trend_integrator.generate_report(scheme=scheme, topn=3))
        except Exception as e:
            parts.append(f"主升浪清單生成失敗：{e}")
    else:
        parts.append("（虛擬貨幣模組已關閉）")

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
    return {"ok": True, "tag": "v8R7", "ts": int(time.time())}
