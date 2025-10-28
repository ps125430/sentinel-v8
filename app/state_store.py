from __future__ import annotations
import json, os, tempfile, shutil, time
from typing import Any, Dict

STATE_PATH = os.environ.get("SENTINEL_STATE", "/tmp/sentinel-v8.json")
DEFAULT_STATE: Dict[str, Any] = {
    "prefs": { "color_scheme": "tw" },   # tw=多紅空綠, us=多綠空紅
    "watches": {},                       # "BTC": {"until": 0, "last_alert": 0}
}

def _atomic_write(path: str, data: Dict[str, Any]) -> None:
    d = os.path.dirname(path)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix="sentinel_", suffix=".json", dir=d if d else ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        shutil.move(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except Exception:
            pass

def _merge_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
    out = DEFAULT_STATE.copy()
    out["prefs"] = {**DEFAULT_STATE["prefs"], **(data.get("prefs") or {})}
    out["watches"] = data.get("watches") or {}
    # 修補 last_alert 欄位
    for k, v in list(out["watches"].items()):
        if not isinstance(v, dict):
            out["watches"][k] = {"until": int(v) or 0, "last_alert": 0}
        else:
            v.setdefault("last_alert", 0)
    return out

def load_state() -> Dict[str, Any]:
    if not os.path.exists(STATE_PATH):
        return DEFAULT_STATE.copy()
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _merge_defaults(data)
    except Exception:
        return DEFAULT_STATE.copy()

_state_cache: Dict[str, Any] | None = None

def get_state() -> Dict[str, Any]:
    global _state_cache
    if _state_cache is None:
        _state_cache = load_state()
    return _state_cache

def save_state() -> None:
    global _state_cache
    if _state_cache is None:
        return
    _atomic_write(STATE_PATH, _state_cache)

# －－ prefs －－
def set_pref(key: str, value: Any) -> None:
    s = get_state()
    s.setdefault("prefs", {})[key] = value
    save_state()

def get_pref(key: str, default: Any = None) -> Any:
    return get_state().get("prefs", {}).get(key, default)

# －－ watches －－
def set_watch(symbol: str, until_ts: int) -> None:
    s = get_state()
    sym = symbol.upper()
    item = s.setdefault("watches", {}).get(sym, {"until": 0, "last_alert": 0})
    # 若延長，保留 last_alert；若新建，初始化
    item["until"] = max(until_ts, int(time.time()))
    item.setdefault("last_alert", 0)
    s["watches"][sym] = item
    save_state()

def del_watch(symbol: str) -> None:
    s = get_state()
    s.setdefault("watches", {}).pop(symbol.upper(), None)
    save_state()

def list_watches() -> Dict[str, Any]:
    return get_state().get("watches", {})

def cleanup_expired(now_ts: int | None = None) -> bool:
    now = int(now_ts or time.time())
    s = get_state()
    ws = s.get("watches", {})
    changed = False
    for k, v in list(ws.items()):
        if v.get("until", 0) < now:
            ws.pop(k, None)
            changed = True
    if changed:
        save_state()
    return changed
